# Spec Schema 契约

日期：2026-06-10
状态：`attestflow go` approval boundary 已实现

## 目标

`attestflow go` 的任何输入都是 requirement source，不是执行许可。无论输入是内联文字、Markdown、TXT、DOCX 还是 PDF，它只代表“有一份需求来源需要整理”。执行许可只能来自 approved spec。

这条边界防止原始 PRD、Issue、评审意见或一句话目标直接变成 ready task。`go` 必须先把 source 收敛成 `spec.md`，解决 open questions，再由 `approval.json` 明确批准，之后才能把 approved spec 交给 planner。

## Runtime Layout

Spec runtime 根目录由 `paths.specs` 配置，默认是：

```text
harness/specs/
  SPEC-0001/
    spec.md
    approval.json
  sources/
    <content-hash>-<format>/
      source.json
      source.txt
```

`harness/specs/SPEC-0001/spec.md` 是可审阅、可批准的需求规格。`approval.json` 是该 spec 的批准元数据。

Source evidence 写在 `harness/specs/sources/...` 下。`source.json` 保存来源类型、格式、路径、hash 和接收时间；`source.txt` 保存从文件来源抽取出的文本。内联文字来源只需要 `source.json`，其中保留原始输入。

## Spec Content

v1 deterministic minimum required headings 只包含当前 `require_approved_spec` 会硬校验的结构：

- `Goal`：批准后的目标。
- `Acceptance Criteria`：批准后的验收标准。
- `Open Questions`：未解决问题。

`Open Questions` 是执行门禁字段。它必须为 `None`、`无` 或空，才能进入 planner。

生成的 draft spec template 还会包含推荐 review sections，用来帮助人和 Agent 审阅来源并收敛边界：

- `Source Evidence`：指向 `harness/specs/sources/.../source.json`。
- `Confirmed Requirements`：已经确认的需求。
- `Scope`：本轮执行范围。
- `Out Of Scope`：明确不做的范围。
- `Source Summary`：来源文本摘要或原文片段。

这些推荐 section 是 draft template 和 review workflow 的一部分，不是 v1 `require_approved_spec` 的 deterministic hard gate。项目可以在批准前保留、扩展或重写它们；真正的硬门禁仍是上面的 three-heading minimum、有效 `approval.json` 和已解决的 `Open Questions`。

## Approval Rules

- `approval.json.status` 必须是 `approved` 才能进入 planner。
- `approval.json.schema_version` 必须是 `1`。
- `approval.json.spec_id` 必须等于 spec 目录名，例如 `SPEC-0001`。
- `approval.json.approved_by` 和 `approval.json.approved_at` 必须是非空字符串。
- `spec.md` 的 `Open Questions` 必须为 `None`、`无` 或空。
- 非交互模式必须显式传入 `--from-spec`、`--approve` 和 `--non-interactive`。
- `--from-spec` 必须位于 configured specs dir 下，并且路径形状必须是 `SPEC-####/spec.md`。
- configured specs dir 必须位于项目根目录下。
- 文档解析失败、扫描 PDF、缺少 DOCX/PDF optional deps、不可读 source 或空 source 都不能进入 planner。

## Planner Boundary

`go` 传给 planner 的是 approved spec 内容和上下文，不是 raw user text、raw PRD 或 source evidence。Planner provider 不应从 raw source 推断 approval，也不能把 raw source 当成已经澄清的执行边界。

如果 spec 缺批准、批准无效或仍有 open questions，Attestflow 必须 fail closed，不创建 ready task，不启动 autopilot。
