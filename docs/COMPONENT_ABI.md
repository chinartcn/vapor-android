# 组件 ABI（Vapor.component）

> 目标：让「UI 组件」像渲染扩展一样**可分发、可热插拔**——写一个 Python 模块或一个
> C 外壳，丢进逻辑包的 `lib/`，在 `abi.json` 里打开即可，不需要改 Rust、
> 不需要重编 APK。

组件 ABI 复用已有的稳定 C ABI（`VaporApi` 的 `py_eval` 等能力），**没有新增函数表**，
因此旧宿主加载新组件时最多是「不认识、跳过」，不会崩。

---

## 1. 两层契约

```
lib/
  hello_component.py        ← 组件本体（Python）：ABI / manifest() / register(host)
  libcomponent_abi.so       ← 可选 C 外壳：vapor_component_init(VaporApi*)
  abi.json                  ← 扩展清单（kind=python / kind=c）
```

| 层 | 语言 | 职责 | 入口 |
|---|---|---|---|
| 组件提供者 | Python | 定义组件类、声明 props、注册 | `ABI` / `manifest()` / `register(host)` |
| C 外壳 | C | 分发容器：定位自身目录、校验授权、注册同目录的 Python 包 | `vapor_component_init(VaporApi*)` |

C 直接建 UI 节点没有意义（节点是 Python 对象），所以约定 C 只做「搬运」：
`dladdr` 找到自己所在目录 → `api->py_eval` 一段注册脚本。

---

## 2. Python 提供者规范（ABI v1）

```python
from Vapor import ui as V

ABI = 1

class MyCard(V.Component):
    props = {"title": ""}
    template = '<View bg="#141A22" radius="12" padding="14"><Text :text="title" /></View>'
    def setup(self, props, ctx):
        return {}

def manifest():
    return {
        "abi": 1,
        "name": "my-ext",
        "version": "1.0.0",
        "components": [
            {"name": "MyCard", "props": dict(MyCard.props), "description": "卡片"},
        ],
    }

def register(host):
    host.register(MyCard)
```

`manifest()` 字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `abi` | 否（默认 1） | 不能高于宿主 `COMPONENT_ABI_VERSION` |
| `name` | 是 | 扩展标识（如 `vapor.statcard`） |
| `version` | 否 | 语义化版本，展示用 |
| `components` | 是 | 非空列表，每项 `{"name", "props"?, "description"?}` |

`register(host)` 里通过 `host.register(组件类)` 注册。宿主句柄还提供
`host.components`（当前注册表）与 `host.abi`。

---

## 3. 校验规则

`Vapor.component.register_provider(mod)` 会依次：

1. 检查 `manifest` / `register` 都是可调用对象；
2. 校验 `abi <= COMPONENT_ABI_VERSION`；
3. 校验 `name` 非空、`components` 非空、组件名不重复、`props` 是 dict；
4. 调 `register(host)`；
5. **回查**：清单里声明的组件必须都出现在 `Vapor.ui.COMPONENTS` 里，否则报
   `ProviderError("manifest 声明了但这些组件没有注册: ...")`。

任何一步失败都会抛 `ProviderError`，调用方（扩展加载器）记录日志并继续，
不会影响其它扩展。

---

## 4. 模板生成

```bash
python -m Vapor.component MyCard                 # lib/MyCard.py
python -m Vapor.component MyCard --kind c        # lib/MyCard.c（C 外壳骨架）
python -m Vapor.component MyCard --out protocol/example/lib
```

生成的 Python 模板已经包含：声明式 `props`、默认插槽、`manifest()` 与 `register()`，
并附带 `abi.json` 的登记片段；C 模板包含 `dladdr` + `py_eval` 的完整实现。

（生成器在 `python/Vapor/component.py`，模板用 `@NAME@` / `@MODULE@` 占位，
避免 `str.format` 和 C/Python 代码里的花括号冲突。）

---

## 5. C 外壳契约

`include/vapor_component.h`：

```c
#define VAPOR_COMPONENT_ABI_VERSION 1
#define VAPOR_COMPONENT_ENTRY "vapor_component_init"
typedef int32_t (*VaporComponentInitFn)(VaporApi* api);
```

`protocol/example/lib/component_abi.c` 是可直接编译的示例：

```bash
$CC -shared -fPIC -Iinclude protocol/example/lib/component_abi.c \
    -o protocol/example/lib/libcomponent_abi.so
```

它做的事：`dladdr` 取自身路径 → 目录插到 `sys.path` → `component.load_provider("hello_component")`
→ 校验并注册。返回值 `VAPOR_OK` 表示成功。

---

## 6. 打包与加载

`lib/abi.json` 里登记（默认关闭，按需打开）：

```json
{"name": "hello_component", "kind": "python", "path": "hello_component.py",
 "arch": "all", "enabled": true}
{"name": "component_abi", "kind": "c", "path": "libcomponent_abi.so",
 "symbol": "vapor_component_init", "arch": "all", "enabled": true}
```

加载顺序沿用既有扩展机制：`abi.json` 顺序 → `kind=python` 走 `importlib`、
`kind=c` 走 `dlopen` + 入口符号。组件扩展失败不影响宿主启动。

---

## 7. 版本演进

* `COMPONENT_ABI_VERSION` 只在**破坏性变更**时 +1；新增 manifest 字段视为向后兼容；
* 提供者 `ABI > 宿主` 时拒绝加载（避免用到不存在的宿主能力）；
* C 外壳入口符号固定为 `vapor_component_init`，签名不变；
* 组件名冲突时后注册的会覆盖先注册的（`ui.register` 直接写表），需要并存请用命名空间前缀。

---

## 7.5 预设组件也是提供者

`Vapor.presets` 本身实现了同一套契约：`manifest()` 列出 12 个预设组件与 props，
`register(host)` 把它们注册进宿主。扩展可以：

* 直接 `<Card>` / `<Chip>` 组合使用；
* 继承预设类（`class MyCard(presets.Card)`）覆盖 `build` / `props`；
* 在 manifest 里声明"依赖 vapor.presets"（宿主按加载顺序保证预设先注册）。

---

## 8. 示例与测试

* 示例组件：`protocol/example/lib/hello_component.py`（`StatCard`：标题 + 数值 + 强调色 + 插槽 + 点击）；
* 测试：`python/tests/test_vapor_component.py`（模板生成、ABI 校验负例、注册后实际渲染、示例组件渲染）；
* 文档索引见 `docs/REACTIVE.md` 第 5 节。
