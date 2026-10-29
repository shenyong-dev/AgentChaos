"""Export the T02 field dictionary and ER diagram from SQLAlchemy metadata."""

import json

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import mysql

from app.core.config import PROJECT_ROOT
from app.models import Base

PURPOSES = {
    "ac_users": "本地演示账号与operator/reviewer角色",
    "ac_targets": "被测对象登记，不表示已实现Agent",
    "ac_target_versions": "不可变对象/模型/工具/知识配置修订",
    "ac_knowledge_documents": "有来源与适用条件的不可变规则修订",
    "ac_test_cases": "用例修订、家族/划分、审核和独立预期",
    "ac_fault_profiles": "版本化故障配置；T07实现注入",
    "ac_test_runs": "冻结运行清单与可变运行状态",
    "ac_test_executions": "独立计划执行、scope/session、恢复attempt及租约",
    "ac_trace_events": "T06追加式类型化事件、真实run归属与事务提交序列",
    "ac_assertion_results": "追加式规则/AI/人工判定；T08实现断言引擎",
    "ac_defects": "缺陷归并与人工复核，不覆盖原始判定",
    "ac_artifacts": "不可变清单；运行归属run，T11—T13生成/校准/冻结归属owner，二者互斥",
    "sb_rooms": "按scope隔离的场地和开放时间",
    "sb_booking_drafts": "绑定身份/场地/参数哈希的确认草稿",
    "sb_bookings": "预约事实、稳定幂等键和草稿唯一性",
    "sb_booking_slots": "按scope/场地/30分钟占用片防冲突",
}


def build_schema():
    config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
    result = {"revision": ScriptDirectory.from_config(config).get_current_head(), "tables": []}
    for table in Base.metadata.sorted_tables:
        columns = []
        for column in table.columns:
            columns.append(
                {
                    "name": column.name,
                    "type": column.type.compile(dialect=mysql.dialect()),
                    "nullable": column.nullable,
                    "primary_key": column.primary_key,
                    "server_default": str(column.server_default.arg)
                    if column.server_default
                    else None,
                    "application_default": (
                        getattr(column.default.arg, "__name__", "callable")
                        if column.default and callable(column.default.arg)
                        else str(column.default.arg)
                        if column.default
                        else None
                    ),
                }
            )
        constraints = []
        for constraint in sorted(table.constraints, key=lambda item: str(item.name)):
            if isinstance(constraint, ForeignKeyConstraint):
                constraints.append(
                    {
                        "kind": "foreign_key",
                        "name": str(constraint.name),
                        "columns": [column.name for column in constraint.columns],
                        "references": [element.target_fullname for element in constraint.elements],
                    }
                )
            elif isinstance(constraint, UniqueConstraint):
                constraints.append(
                    {
                        "kind": "unique",
                        "name": str(constraint.name),
                        "columns": [column.name for column in constraint.columns],
                    }
                )
            elif isinstance(constraint, CheckConstraint):
                constraints.append(
                    {"kind": "check", "name": str(constraint.name), "sql": str(constraint.sqltext)}
                )
        result["tables"].append(
            {
                "name": table.name,
                "purpose": PURPOSES[table.name],
                "columns": columns,
                "constraints": constraints,
                "indexes": [
                    {
                        "name": index.name,
                        "columns": [column.name for column in index.columns],
                        "unique": index.unique,
                    }
                    for index in sorted(table.indexes, key=lambda item: item.name)
                ],
            }
        )
    return result


def main():
    schema = build_schema()
    root = PROJECT_ROOT / "docs" / "architecture"
    (root / "database.schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# T02—T13 数据库字段字典与关系",
        "",
        f"迁移：`{schema['revision']}`。12张平台表+4张实验业务表；另有Alembic自身的版本表。",
        "",
        "由 backend/scripts/export_database_docs.py 生成；约束/索引为应用显式声明。"
        "MySQL可能自动建立外键支持索引；session见T04，run事件序列及可空execution见T06，owner证据见T11/T12/T13验收记录。",
        "",
        "所有表为InnoDB/utf8mb4；符号标识使用区分大小写的utf8mb4_bin。UTCDateTime要求带时区输入，转UTC存DATETIME(6)，读取时补UTC。JSON赋值复制并拒绝NaN；修改可变JSON字段应赋新值。",
        "",
        "固定版本/审核后用例/运行清单及证据由迁移中的23个触发器保护；"
        "规则见 docs/database-operations.md。Pydantic冻结或ORM字段本身不能代替数据库保护。",
        "",
        "## ER图",
        "",
        "```mermaid",
        "erDiagram",
    ]
    for table in schema["tables"]:
        lines += [f"    {table['name'].upper()} {{", "        BIGINT id PK", "    }"]
    for table in Base.metadata.sorted_tables:
        for fk in sorted(table.foreign_key_constraints, key=lambda item: str(item.name)):
            parent = fk.referred_table.name.upper()
            marker = "o|" if any(column.nullable for column in fk.columns) else "||"
            lines.append(f"    {parent} {marker}--o{{ {table.name.upper()} : {fk.name}")
    lines += [
        "```",
        "",
        "复合外键作为一组表达同scope/actor/room的关联；图不替代字段与约束清单。",
        "",
    ]
    for table in schema["tables"]:
        lines += [
            f"## {table['name']}",
            "",
            table["purpose"],
            "",
            "| 字段 | MySQL类型 | NULL | 默认值 |",
            "|---|---|---|---|",
        ]
        for column in table["columns"]:
            default = column["server_default"] or column["application_default"] or "—"
            pk = " PK" if column["primary_key"] else ""
            lines.append(
                f"| `{column['name']}`{pk} | `{column['type']}` | "
                f"{'是' if column['nullable'] else '否'} | `{default}` |"
            )
        lines += ["", "约束：", ""]
        for constraint in table["constraints"]:
            description = constraint.get("sql") or ", ".join(constraint["columns"])
            if constraint["kind"] == "foreign_key":
                description += " → " + ", ".join(constraint["references"])
            lines.append(f"- `{constraint['name']}`（{constraint['kind']}）：{description}")
        for index in table["indexes"]:
            lines.append(f"- 索引 `{index['name']}`：{', '.join(index['columns'])}")
        lines += [""]
    (root / "database.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Exported {len(schema['tables'])} table dictionaries and ER relationships.")


if __name__ == "__main__":
    main()
