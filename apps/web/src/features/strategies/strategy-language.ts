import type { Monaco } from "@monaco-editor/react";
import type { editor, Position } from "monaco-editor";

type CompletionItem = {
  label: string;
  kind: "class" | "method" | "field" | "snippet";
  insertText: string;
  detail: string;
  documentation: string;
};

const CLASS_SKELETON = `from qtrade import Strategy


class MyStrategy(Strategy):
    """说明策略假设与适用行情。"""

    def on_start(self):
        self.quantity = int(self.parameters.get("quantity", 100))

    def on_bar(self, bar):
        # 只能使用当前及历史数据；下单会在引擎侧按成交规则处理。
        pass
`;

const TOP_LEVEL: CompletionItem[] = [
  {
    label: "from qtrade import Strategy",
    kind: "snippet",
    insertText: "from qtrade import Strategy",
    detail: "导入策略基类",
    documentation: "策略源码必须从 qtrade 导入 Strategy，并恰好定义一个子类。",
  },
  {
    label: "Strategy class skeleton",
    kind: "snippet",
    insertText: CLASS_SKELETON,
    detail: "可运行的策略骨架",
    documentation:
      "生成一个包含 on_start 与 on_bar 的策略骨架，保存后仍需通过静态校验。",
  },
];

const SELF_MEMBERS: CompletionItem[] = [
  {
    label: "parameters",
    kind: "field",
    insertText: "parameters",
    detail: "dict[str, object]",
    documentation:
      "版本参数，来自回测表单或参数校验；在 on_start 中读取并做范围检查。",
  },
  {
    label: "buy",
    kind: "method",
    insertText: "buy(quantity)",
    detail: "buy(quantity, **options)",
    documentation:
      "买入提议的简写。默认使用当前 bar 的标的；数量必须为正。",
  },
  {
    label: "sell",
    kind: "method",
    insertText: "sell(quantity)",
    detail: "sell(quantity, **options)",
    documentation:
      "卖出提议的简写。可卖数量受 T+1 与持仓约束，最终由引擎判断。",
  },
  {
    label: "order",
    kind: "method",
    insertText: 'order("buy", quantity)',
    detail: "order(side, quantity, *, ...)",
    documentation:
      "完整下单提议：side 为 buy/sell；支持 market、limit、stop、stop_limit 与 day/ioc/fok。",
  },
];

const BAR_FIELDS: CompletionItem[] = [
  ["instrument_id", "标的代码，例如 600519.SH"],
  ["timestamp", "带时区的 bar 时间戳"],
  ["open", "Decimal 开盘价"],
  ["high", "Decimal 最高价"],
  ["low", "Decimal 最低价"],
  ["close", "Decimal 收盘价"],
  ["volume", "Decimal 成交量"],
  ["value", "Decimal 成交额，可能为空"],
  ["granularity", "bar 粒度，例如 1d"],
  ["quality", "valid / suspect / quarantined"],
].map(([field, description]) => ({
  label: field,
  kind: "field" as const,
  insertText: field,
  detail: description,
  documentation: `bar.${field}：${description}`,
}));

const HOVER_DOCS: Record<string, string> = {
  Strategy:
    "策略基类。一个策略源码文件必须恰好定义一个 `Strategy` 子类，并由隔离 Runner 执行。",
  on_start:
    "生命周期回调，在参数注入后执行一次。适合读取 `self.parameters` 并做参数范围检查。",
  on_bar:
    "生命周期回调，按历史顺序每根 bar 调用一次。只能读取当前及历史数据；不能访问网络、文件或动态执行。",
  order: [
    "```python",
    'order(side, quantity, *, instrument_id=None, order_type="market",',
    "      limit_price=None, stop_price=None, time_in_force=\"day\")",
    "```",
    "产生下单提议。side 为 `buy`/`sell`；限价与止损字段必须与订单类型匹配。",
  ].join("\n"),
  buy: "`buy(quantity, **options)`：`order(\"buy\", ...)` 的简写。数量必须为正。",
  sell: "`sell(quantity, **options)`：`order(\"sell\", ...)` 的简写。受 T+1 与持仓约束。",
  parameters:
    "冻结版本的参数字典。键必须在版本参数 schema 的 `properties` 中，保存后不可修改。",
  bar: "当前 bar。字段包括 `instrument_id`、`timestamp`、`open`、`high`、`low`、`close`、`volume`、`value`、`granularity`、`quality`。",
};

let registered = false;

export function registerStrategyLanguageSupport(monaco: Monaco): void {
  if (registered) return;
  registered = true;

  const kindOf = (kind: CompletionItem["kind"]) => {
    switch (kind) {
      case "class":
        return monaco.languages.CompletionItemKind.Class;
      case "method":
        return monaco.languages.CompletionItemKind.Method;
      case "field":
        return monaco.languages.CompletionItemKind.Field;
      default:
        return monaco.languages.CompletionItemKind.Snippet;
    }
  };

  monaco.languages.registerCompletionItemProvider("python", {
    triggerCharacters: ["."],
    provideCompletionItems(model: editor.ITextModel, position: Position) {
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };
      const before = model
        .getLineContent(position.lineNumber)
        .slice(0, position.column - 1)
        .trimEnd();
      const items = before.endsWith("bar.")
        ? BAR_FIELDS
        : before.endsWith("self.")
          ? SELF_MEMBERS
          : TOP_LEVEL;
      return {
        suggestions: items.map((item) => ({
          label: item.label,
          kind: kindOf(item.kind),
          insertText: item.insertText,
          insertTextRules:
            item.kind === "snippet"
              ? monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet
              : undefined,
          detail: item.detail,
          documentation: item.documentation,
          range,
        })),
      };
    },
  });

  monaco.languages.registerHoverProvider("python", {
    provideHover(model: editor.ITextModel, position: Position) {
      const word = model.getWordAtPosition(position);
      if (!word) return null;
      const value = HOVER_DOCS[word.word];
      if (!value) return null;
      return {
        range: {
          startLineNumber: position.lineNumber,
          endLineNumber: position.lineNumber,
          startColumn: word.startColumn,
          endColumn: word.endColumn,
        },
        contents: [{ value }],
      };
    },
  });
}