"""组件扩展示例：StatCard（指标卡片）。

演示组件 ABI v1 的三件事：
  * 声明式 props（宿主/市场可以读 manifest 展示）
  * 默认插槽 + 点击事件
  * 通过 manifest/register 注册自己

在 lib/abi.json 里打开本扩展后，逻辑包的模板里就能直接写：

    <StatCard title="下载" value="1.2k" accent="#7BD88F" @click="on_card" />
"""

from __future__ import annotations

from Vapor import ui as V

ABI = 1


class StatCard(V.Component):
    props = {
        "title": "",
        "value": "",
        "accent": "#2D7FF9",
        "bg": "#141A22",
    }

    template = '''
      <View :bg="bg" radius="14" padding="16" gap="6" @click="on_click">
        <Row gap="8" align="center">
          <View :bg="accent" w="4" h="28" radius="2" />
          <Text :text="title" size="13" color="#8A94A6" />
        </Row>
        <Text :text="value" size="28" color="#E6EAF0" />
        <slot />
      </View>
    '''

    def setup(self, props, ctx):
        return {
            "on_click": lambda ev: props.get("on_click") and props["on_click"](ev),
        }


def manifest():
    return {
        "abi": ABI,
        "name": "vapor.statcard",
        "version": "1.0.0",
        "components": [
            {
                "name": "StatCard",
                "props": dict(StatCard.props),
                "description": "指标卡片：标题 + 数值 + 强调色 + 默认插槽",
            },
        ],
    }


def register(host):
    host.register(StatCard)
