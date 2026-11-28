//! alien-signals 风格的细粒度响应式内核。
//!
//! 设计要点（对齐 Vue 3.6 / alien-signals 的模型）：
//!   * **统一节点 + 双向链表**：signal / computed / effect 是同一种 Node，依赖边是
//!     Link，同时挂在 dep 的 subs 链和 sub 的 deps 链上，增删都是 O(1)。
//!   * **推送 + 拉取**：写 signal 时向下推送（把依赖它的 computed 标脏、把 effect
//!     入队），读 computed 时按脏标记惰性重算，计算只发生一次。
//!   * **不建引用环**：节点间仅用 u32 下标，Rust 侧无 Rc 环，停止/释放是显式的。
//!   * **Python 对象只在调用点持有**：所有 with(...) 借用都在调用 Python 之前释放，
//!     避免 RefCell 重入 panic。

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::cell::RefCell;
use std::collections::VecDeque;

pub type Id = u32;
pub const NONE: Id = u32::MAX;

pub const F_SIGNAL: u8 = 1 << 0;
pub const F_COMPUTED: u8 = 1 << 1;
pub const F_EFFECT: u8 = 1 << 2;

/// 单帧内 effect 重跑的硬上限，防止自激写入死循环卡死 UI 线程。
const MAX_FLUSH_ITERS: u32 = 100_000;

pub struct Node {
    pub flags: u8,
    /// signal / computed 的当前值。
    pub value: Option<Py<PyAny>>,
    /// computed 的 getter / effect 的函数体。
    pub fnc: Option<Py<PyAny>>,
    /// effect 的自定义调度器（watch 用它做去重）。
    pub scheduler: Option<Py<PyAny>>,
    pub version: u64,
    // ---- subs 链表（谁依赖我）----
    pub subs: Id,
    pub subs_tail: Id,
    // ---- deps 链表（我依赖谁）----
    pub deps: Id,
    pub deps_tail: Id,
    pub dirty: bool,
    pub queued: bool,
    pub paused: bool,
    pub scope: Id,
}

impl Node {
    fn new(flags: u8) -> Self {
        Node {
            flags,
            value: None,
            fnc: None,
            scheduler: None,
            version: 0,
            subs: NONE,
            subs_tail: NONE,
            deps: NONE,
            deps_tail: NONE,
            dirty: flags & F_COMPUTED != 0,
            queued: false,
            paused: false,
            scope: NONE,
        }
    }
}

#[derive(Clone, Copy)]
pub struct Link {
    pub dep: Id,
    pub sub: Id,
    pub prev_sub: Id,
    pub next_sub: Id,
    pub prev_dep: Id,
    pub next_dep: Id,
    pub version: u64,
}

pub struct Arena {
    pub nodes: Vec<Node>,
    pub free: Vec<Id>,
    pub links: Vec<Link>,
    pub free_links: Vec<Id>,
    /// 正在追踪依赖的节点（effect / computed）。NONE 表示不追踪。
    pub current_sub: Id,
    pub batch_depth: u32,
    pub flushing: bool,
    pub pending: VecDeque<Id>,
    pub scopes: Vec<Vec<Id>>,
    pub scope_stack: Vec<Id>,
    pub effect_count: usize,
    pub computed_count: usize,
    pub run_count: u64,
}

impl Default for Arena {
    fn default() -> Self {
        Arena {
            nodes: Vec::with_capacity(64),
            free: Vec::new(),
            links: Vec::with_capacity(64),
            free_links: Vec::new(),
            current_sub: NONE,
            batch_depth: 0,
            flushing: false,
            pending: VecDeque::new(),
            scopes: Vec::new(),
            scope_stack: Vec::new(),
            effect_count: 0,
            computed_count: 0,
            run_count: 0,
        }
    }
}

thread_local! {
    static ARENA: RefCell<Arena> = RefCell::new(Arena::default());
}

#[inline]
pub fn with<R>(f: impl FnOnce(&mut Arena) -> R) -> R {
    ARENA.with(|cell| f(&mut cell.borrow_mut()))
}

impl Arena {
    // ---------------- 节点分配 ----------------

    fn alloc_node(&mut self, flags: u8) -> Id {
        let id = match self.free.pop() {
            Some(id) => id,
            None => {
                self.nodes.push(Node::new(0));
                (self.nodes.len() - 1) as Id
            }
        };
        self.nodes[id as usize] = Node::new(flags);
        id
    }

    fn alloc_link(&mut self) -> Id {
        match self.free_links.pop() {
            Some(id) => {
                self.links[id as usize] = Link {
                    dep: NONE,
                    sub: NONE,
                    prev_sub: NONE,
                    next_sub: NONE,
                    prev_dep: NONE,
                    next_dep: NONE,
                    version: 0,
                };
                id
            }
            None => {
                self.links.push(Link {
                    dep: NONE,
                    sub: NONE,
                    prev_sub: NONE,
                    next_sub: NONE,
                    prev_dep: NONE,
                    next_dep: NONE,
                    version: 0,
                });
                (self.links.len() - 1) as Id
            }
        }
    }

    // ---------------- 依赖边 ----------------

    fn add_dep_link(&mut self, dep: Id, sub: Id) {
        let lid = self.alloc_link();
        let dep_version = self.nodes[dep as usize].version;

        // 挂到 dep 的 subs 尾部
        let dep_tail = self.nodes[dep as usize].subs_tail;
        {
            let l = &mut self.links[lid as usize];
            l.dep = dep;
            l.sub = sub;
            l.version = dep_version;
            l.prev_sub = dep_tail;
            l.next_sub = NONE;
        }
        if dep_tail != NONE {
            self.links[dep_tail as usize].next_sub = lid;
        } else {
            self.nodes[dep as usize].subs = lid;
        }
        self.nodes[dep as usize].subs_tail = lid;

        // 挂到 sub 的 deps 尾部
        let sub_tail = self.nodes[sub as usize].deps_tail;
        {
            let l = &mut self.links[lid as usize];
            l.prev_dep = sub_tail;
            l.next_dep = NONE;
        }
        if sub_tail != NONE {
            self.links[sub_tail as usize].next_dep = lid;
        } else {
            self.nodes[sub as usize].deps = lid;
        }
        self.nodes[sub as usize].deps_tail = lid;
    }

    fn drop_link(&mut self, lid: Id) {
        let (dep, sub, ps, ns, pd, nd) = {
            let l = &self.links[lid as usize];
            (l.dep, l.sub, l.prev_sub, l.next_sub, l.prev_dep, l.next_dep)
        };
        if ps != NONE {
            self.links[ps as usize].next_sub = ns;
        } else {
            self.nodes[dep as usize].subs = ns;
        }
        if ns != NONE {
            self.links[ns as usize].prev_sub = ps;
        } else {
            self.nodes[dep as usize].subs_tail = ps;
        }
        if pd != NONE {
            self.links[pd as usize].next_dep = nd;
        } else {
            self.nodes[sub as usize].deps = nd;
        }
        if nd != NONE {
            self.links[nd as usize].prev_dep = pd;
        } else {
            self.nodes[sub as usize].deps_tail = pd;
        }
        self.links[lid as usize].dep = NONE;
        self.links[lid as usize].sub = NONE;
        self.free_links.push(lid);
    }

    /// 断开某个节点作为 sub 的全部依赖（effect 重跑前 / stop 时）。
    pub fn clear_deps(&mut self, sub: Id) {
        let mut lid = self.nodes[sub as usize].deps;
        while lid != NONE {
            let next = self.links[lid as usize].next_dep;
            self.drop_link(lid);
            lid = next;
        }
        self.nodes[sub as usize].deps = NONE;
        self.nodes[sub as usize].deps_tail = NONE;
    }

    /// 断开某个节点作为 dep 的全部订阅（销毁时）。
    pub fn clear_subs(&mut self, dep: Id) {
        let mut lid = self.nodes[dep as usize].subs;
        while lid != NONE {
            let next = self.links[lid as usize].next_sub;
            self.drop_link(lid);
            lid = next;
        }
        self.nodes[dep as usize].subs = NONE;
        self.nodes[dep as usize].subs_tail = NONE;
    }

    fn has_dep(&self, sub: Id, dep: Id) -> bool {
        let mut lid = self.nodes[sub as usize].deps;
        while lid != NONE {
            if self.links[lid as usize].dep == dep {
                return true;
            }
            lid = self.links[lid as usize].next_dep;
        }
        false
    }

    /// 记录依赖：当前追踪上下文 current_sub 依赖 dep。
    pub fn track(&mut self, dep: Id) {
        let sub = self.current_sub;
        if sub == NONE || sub == dep {
            return;
        }
        if self.has_dep(sub, dep) {
            return;
        }
        self.add_dep_link(dep, sub);
    }

    /// 向下传播：把 computed 标脏，把 effect 入队。
    pub fn propagate(&mut self, dep: Id) {
        let mut stack: Vec<Id> = vec![dep];
        let mut guard: u32 = 0;
        while let Some(n) = stack.pop() {
            guard += 1;
            if guard > MAX_FLUSH_ITERS {
                break;
            }
            let mut lid = self.nodes[n as usize].subs;
            while lid != NONE {
                let (sub, next) = {
                    let l = &self.links[lid as usize];
                    (l.sub, l.next_sub)
                };
                let flags = self.nodes[sub as usize].flags;
                if flags & F_EFFECT != 0 {
                    if !self.nodes[sub as usize].queued && !self.nodes[sub as usize].paused {
                        self.nodes[sub as usize].queued = true;
                        self.pending.push_back(sub);
                    }
                } else if flags & F_COMPUTED != 0 {
                    // 只有 clean -> dirty 那一次才继续向下传播
                    if !self.nodes[sub as usize].dirty {
                        self.nodes[sub as usize].dirty = true;
                        stack.push(sub);
                    }
                }
                lid = next;
            }
        }
    }

    fn attach_to_scope(&mut self, id: Id) {
        if let Some(&scope) = self.scope_stack.last() {
            self.scopes[scope as usize].push(id);
            self.nodes[id as usize].scope = scope;
        }
    }
}

// ---------------------------------------------------------------------------
// signal
// ---------------------------------------------------------------------------

pub fn signal_get(py: Python<'_>, id: Id) -> PyResult<Py<PyAny>> {
    with(|a| {
        a.track(id);
        a.nodes[id as usize]
            .value
            .as_ref()
            .map(|v| v.clone_ref(py))
            .ok_or_else(|| PyRuntimeError::new_err("signal 已被销毁"))
    })
}

pub fn signal_peek(py: Python<'_>, id: Id) -> PyResult<Py<PyAny>> {
    with(|a| {
        a.nodes[id as usize]
            .value
            .as_ref()
            .map(|v| v.clone_ref(py))
            .ok_or_else(|| PyRuntimeError::new_err("signal 已被销毁"))
    })
}

pub fn signal_set(py: Python<'_>, id: Id, value: Py<PyAny>) -> PyResult<()> {
    with(|a| {
        let node = &mut a.nodes[id as usize];
        node.value = Some(value);
        node.version = node.version.wrapping_add(1);
        a.propagate(id);
    });
    flush(py)
}

// ---------------------------------------------------------------------------
// computed：惰性重算
// ---------------------------------------------------------------------------

pub fn computed_get(py: Python<'_>, id: Id) -> PyResult<Py<PyAny>> {
    let dirty = with(|a| a.nodes[id as usize].dirty);
    if dirty {
        refresh_computed(py, id)?;
    }
    signal_get(py, id)
}

pub fn computed_peek(py: Python<'_>, id: Id) -> PyResult<Py<PyAny>> {
    let dirty = with(|a| a.nodes[id as usize].dirty);
    if dirty {
        refresh_computed(py, id)?;
    }
    signal_peek(py, id)
}

fn refresh_computed(py: Python<'_>, id: Id) -> PyResult<()> {
    let fnc = with(|a| {
        a.nodes[id as usize]
            .fnc
            .as_ref()
            .map(|f| f.clone_ref(py))
            .ok_or_else(|| PyRuntimeError::new_err("computed 缺少 getter"))
    });

    let fnc = match fnc {
        Ok(f) => f,
        Err(e) => return Err(e),
    };

    // 保存调用方的追踪上下文：computed 内部求值会临时切到 computed 自己，
    // 结束后必须恢复，否则外层 effect 无法订阅到这个 computed（更新不触发）。
    let prev = with(|a| {
        let prev = a.current_sub;
        a.clear_deps(id);
        a.current_sub = id;
        prev
    });
    let out = fnc.bind(py).call0();
    with(|a| {
        a.current_sub = prev;
        a.run_count = a.run_count.wrapping_add(1);
    });
    let value = out?;
    with(|a| {
        let node = &mut a.nodes[id as usize];
        node.value = Some(value.unbind());
        node.version = node.version.wrapping_add(1);
        node.dirty = false;
    });
    Ok(())
}

// ---------------------------------------------------------------------------
// effect
// ---------------------------------------------------------------------------

fn call_fn(py: Python<'_>, f: &Py<PyAny>) -> PyResult<Py<PyAny>> {
    f.bind(py).call0().map(|b| b.unbind())
}

pub fn run_effect(py: Python<'_>, id: Id) -> PyResult<()> {
    let fnc = with(|a| {
        a.nodes[id as usize]
            .fnc
            .as_ref()
            .map(|f| f.clone_ref(py))
            .ok_or_else(|| PyRuntimeError::new_err("effect 缺少函数体"))
    });
    let fnc = match fnc {
        Ok(f) => f,
        Err(e) => return Err(e),
    };
    let paused = with(|a| a.nodes[id as usize].paused);
    if paused {
        return Ok(());
    }
    let prev = with(|a| {
        let prev = a.current_sub;
        a.clear_deps(id);
        a.nodes[id as usize].queued = false;
        a.current_sub = id;
        prev
    });
    let out = call_fn(py, &fnc);
    with(|a| {
        a.current_sub = prev;
        a.run_count = a.run_count.wrapping_add(1);
    });
    out.map(|_| ())
}

/// 执行所有排队的 effect（batch 内不执行）。
pub fn flush(py: Python<'_>) -> PyResult<()> {
    let skip = with(|a| {
        if a.batch_depth > 0 || a.flushing {
            true
        } else {
            a.flushing = true;
            false
        }
    });
    if skip {
        return Ok(());
    }

    let result: PyResult<()> = (|| {
        let mut iters: u32 = 0;
        loop {
            let job = with(|a| {
                if a.batch_depth > 0 {
                    None
                } else {
                    a.pending.pop_front()
                }
            });
            let id = match job {
                Some(id) => id,
                None => break,
            };
            iters += 1;
            if iters > MAX_FLUSH_ITERS {
                with(|a| a.pending.clear());
                return Err(PyRuntimeError::new_err(
                    "响应式更新超过上限：疑似 effect 内部无条件写入自己的依赖",
                ));
            }
            let scheduler = with(|a| {
                a.nodes[id as usize]
                    .scheduler
                    .as_ref()
                    .map(|s| s.clone_ref(py))
            });
            match scheduler {
                // 自定义调度器：把 effect 句柄交给它，由它决定何时 run
                Some(s) => {
                    let handle = Py::new(py, EffectHandle { id })?;
                    let _ = s.bind(py).call1((handle,));
                }
                None => {
                    run_effect(py, id)?;
                }
            }
        }
        Ok(())
    })();

    with(|a| a.flushing = false);
    result
}

pub fn batch(py: Python<'_>, f: Py<PyAny>) -> PyResult<Py<PyAny>> {
    with(|a| a.batch_depth += 1);
    let out = call_fn(py, &f);
    with(|a| a.batch_depth = a.batch_depth.saturating_sub(1));
    let value = out?;
    flush(py)?;
    Ok(value)
}

pub fn untrack(py: Python<'_>, f: Py<PyAny>) -> PyResult<Py<PyAny>> {
    let prev = with(|a| {
        let prev = a.current_sub;
        a.current_sub = NONE;
        prev
    });
    let out = call_fn(py, &f);
    with(|a| a.current_sub = prev);
    out
}

// ---------------------------------------------------------------------------
// Python 侧类
// ---------------------------------------------------------------------------

#[pyclass(module = "Vapor._core", name = "Signal")]
pub struct Signal {
    pub id: Id,
}

#[pymethods]
impl Signal {
    #[new]
    fn new(value: Py<PyAny>) -> Self {
        let id = with(|a| a.alloc_node(F_SIGNAL));
        with(|a| a.nodes[id as usize].value = Some(value));
        Signal { id }
    }

    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        signal_get(py, self.id)
    }

    #[setter]
    fn set_value(&self, py: Python<'_>, value: Py<PyAny>) -> PyResult<()> {
        signal_set(py, self.id, value)
    }

    fn peek(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        signal_peek(py, self.id)
    }

    #[getter]
    fn version(&self) -> u64 {
        with(|a| a.nodes[self.id as usize].version)
    }

    #[getter]
    fn subscriber_count(&self) -> usize {
        with(|a| {
            let mut n = 0usize;
            let mut lid = a.nodes[self.id as usize].subs;
            while lid != NONE {
                n += 1;
                lid = a.links[lid as usize].next_sub;
            }
            n
        })
    }

    #[getter]
    fn id(&self) -> u32 {
        self.id
    }

    fn __repr__(&self) -> String {
        format!("Signal(id={})", self.id)
    }
}

#[pyclass(module = "Vapor._core", name = "Computed")]
pub struct Computed {
    pub id: Id,
}

#[pymethods]
impl Computed {
    #[new]
    fn new(getter: Py<PyAny>) -> Self {
        let id = with(|a| {
            let id = a.alloc_node(F_COMPUTED);
            a.nodes[id as usize].fnc = Some(getter);
            a.computed_count += 1;
            id
        });
        Computed { id }
    }

    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        computed_get(py, self.id)
    }

    fn peek(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        computed_peek(py, self.id)
    }

    #[getter]
    fn dirty(&self) -> bool {
        with(|a| a.nodes[self.id as usize].dirty)
    }

    #[getter]
    fn id(&self) -> u32 {
        self.id
    }

    fn __repr__(&self) -> String {
        format!("Computed(dirty={})", with(|a| a.nodes[self.id as usize].dirty))
    }
}

#[pyclass(module = "Vapor._core", name = "Effect")]
pub struct EffectHandle {
    pub id: Id,
}

#[pymethods]
impl EffectHandle {
    #[new]
    #[pyo3(signature = (fnc, scheduler=None))]
    fn new(py: Python<'_>, fnc: Py<PyAny>, scheduler: Option<Py<PyAny>>) -> PyResult<Self> {
        let id = with(|a| {
            let id = a.alloc_node(F_EFFECT);
            a.nodes[id as usize].fnc = Some(fnc);
            a.nodes[id as usize].scheduler = scheduler;
            a.effect_count += 1;
            a.attach_to_scope(id);
            id
        });
        run_effect(py, id)?;
        Ok(EffectHandle { id })
    }

    /// 立即重跑一次（会重新收集依赖）。
    fn run(&self, py: Python<'_>) -> PyResult<()> {
        run_effect(py, self.id)
    }

    /// 永久停止：断开依赖，之后不会再被触发。
    fn stop(&self) {
        with(|a| {
            a.clear_deps(self.id);
            a.nodes[self.id as usize].queued = false;
            a.nodes[self.id as usize].paused = true;
            a.nodes[self.id as usize].fnc = None;
            a.nodes[self.id as usize].scheduler = None;
        });
    }

    /// 暂停：断开依赖但保留函数体，resume 后重新追踪。
    fn pause(&self) {
        with(|a| {
            a.clear_deps(self.id);
            a.nodes[self.id as usize].queued = false;
            a.nodes[self.id as usize].paused = true;
        });
    }

    fn resume(&self, py: Python<'_>) -> PyResult<()> {
        with(|a| a.nodes[self.id as usize].paused = false);
        run_effect(py, self.id)
    }

    #[getter]
    fn paused(&self) -> bool {
        with(|a| a.nodes[self.id as usize].paused)
    }

    #[getter]
    fn id(&self) -> u32 {
        self.id
    }

    fn __repr__(&self) -> String {
        format!("Effect(id={}, paused={})", self.id, with(|a| a.nodes[self.id as usize].paused))
    }
}

#[pyclass(module = "Vapor._core", name = "Scope")]
pub struct ScopeHandle {
    pub id: Id,
}

#[pymethods]
impl ScopeHandle {
    /// 停止作用域内的全部 effect（组件卸载 / 页面出栈时调用）。
    fn stop(&self) {
        with(|a| {
            if (self.id as usize) >= a.scopes.len() {
                return;
            }
            let ids: Vec<Id> = std::mem::take(&mut a.scopes[self.id as usize]);
            for id in ids {
                a.clear_deps(id);
                let node = &mut a.nodes[id as usize];
                node.queued = false;
                node.paused = true;
                node.fnc = None;
                node.scheduler = None;
            }
        });
    }

    #[getter]
    fn size(&self) -> usize {
        with(|a| {
            if (self.id as usize) < a.scopes.len() {
                a.scopes[self.id as usize].len()
            } else {
                0
            }
        })
    }
}

#[pyfunction]
#[pyo3(signature = (fnc, scheduler=None))]
fn effect(py: Python<'_>, fnc: Py<PyAny>, scheduler: Option<Py<PyAny>>) -> PyResult<EffectHandle> {
    EffectHandle::new(py, fnc, scheduler)
}

#[pyfunction]
fn computed(getter: Py<PyAny>) -> Computed {
    Computed::new(getter)
}

#[pyfunction]
fn signal(value: Py<PyAny>) -> Signal {
    Signal::new(value)
}

#[pyfunction(name = "batch")]
fn batch_call(py: Python<'_>, f: Py<PyAny>) -> PyResult<Py<PyAny>> {
    batch(py, f)
}

#[pyfunction(name = "untrack")]
fn untrack_call(py: Python<'_>, f: Py<PyAny>) -> PyResult<Py<PyAny>> {
    untrack(py, f)
}

#[pyfunction(name = "effect_scope")]
fn scope_fn(py: Python<'_>, f: Py<PyAny>) -> PyResult<ScopeHandle> {
    let id = with(|a| {
        a.scopes.push(Vec::new());
        (a.scopes.len() - 1) as Id
    });
    with(|a| a.scope_stack.push(id));
    let out = call_fn(py, &f);
    with(|a| {
        a.scope_stack.pop();
    });
    out?;
    Ok(ScopeHandle { id })
}

#[pyfunction(name = "flush")]
fn run_flush(py: Python<'_>) -> PyResult<()> {
    flush(py)
}

#[pyfunction]
fn stats(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let (nodes, links, effects, computeds, pending, runs) = with(|a| {
        (
            a.nodes.len(),
            a.links.len(),
            a.effect_count,
            a.computed_count,
            a.pending.len(),
            a.run_count,
        )
    });
    let d = pyo3::types::PyDict::new(py);
    d.set_item("nodes", nodes)?;
    d.set_item("links", links)?;
    d.set_item("effects", effects)?;
    d.set_item("computeds", computeds)?;
    d.set_item("pending", pending)?;
    d.set_item("runs", runs)?;
    Ok(d.into_any().unbind())
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Signal>()?;
    m.add_class::<Computed>()?;
    m.add_class::<EffectHandle>()?;
    m.add_class::<ScopeHandle>()?;
    m.add_function(wrap_pyfunction!(signal, m)?)?;
    m.add_function(wrap_pyfunction!(computed, m)?)?;
    m.add_function(wrap_pyfunction!(effect, m)?)?;
    m.add_function(wrap_pyfunction!(batch_call, m)?)?;
    m.add_function(wrap_pyfunction!(untrack_call, m)?)?;
    m.add_function(wrap_pyfunction!(scope_fn, m)?)?;
    m.add_function(wrap_pyfunction!(run_flush, m)?)?;
    m.add_function(wrap_pyfunction!(stats, m)?)?;
    Ok(())
}
