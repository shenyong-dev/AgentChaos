# AgentChaos 运行报告

运行：5f7ffc80-cdf0-4a19-9bda-ebe3118256fe

计划执行 1；不同用例 1；含恢复尝试 1。

有效规则通过 0 / 1；零分母不提供通过率。

取消、平台错误、证据不足与AI辅助结论分别计数。复核另存，不覆盖原判定；缺陷候选需人工确认。

下列冻结JSON保留分母、排除项、配置、执行ID、原始判定与证据引用。完整事件和文件哈希见同批证据ZIP。

```json
{
  "boundary": "Observed snapshots, original assertions and separately recorded reviews. Fault injection, cancellation, unknown evidence and platform errors are not automatically Agent defects. No formal V0/V1 quality claim.",
  "defects": [
    {
      "category": "rule_violation",
      "defect_key": "ACD-548571bf258e2037ae7e65e7b67fbfacaf922e8c",
      "evidence_refs": [
        "assertion:7",
        "execution:aa412098-8ddc-4d2b-b32a-40e5610e027e",
        "snapshot:aa412098-8ddc-4d2b-b32a-40e5610e027e:after",
        "assertion:14",
        "execution:23ea4125-52c1-4e71-a151-e0f87356e90d",
        "snapshot:23ea4125-52c1-4e71-a151-e0f87356e90d:after"
      ],
      "fingerprint": "548571bf258e2037ae7e65e7b67fbfacaf922e8c48876fbfa305707d8a0bdfc2",
      "first_execution_id": 1,
      "fixed_version_id": null,
      "id": 1,
      "review_artifacts": [],
      "review_note": null,
      "reviewer_id": null,
      "reviews": [],
      "revision_hash": "e31af9de4b50e10c9fadde63a17c450098a57882dc39e4ce869e8e500d33954a",
      "severity": "medium",
      "status": "open",
      "suspected_cause": null,
      "target_id": 1
    }
  ],
  "executions": [
    {
      "after": [],
      "assertions": [
        {
          "actual": {
            "evaluator": {
              "dsl": "1.1",
              "kind": "rule",
              "source_hash": "48062f5f529bd701a13bfb20e0726a2a0ab19ed93ebc230b3fbc26775ed67b2d",
              "sources": {
                "__init__.py": "c6b52f473229bc9a56fe07dd05085f1ef74c5193bda5022f3edb29ee81f8570f",
                "assertion-schema": "6601388185108c270025c72d0203f3d306da2149e3369b92743bd0c88f378477",
                "engine.py": "2fda812429cf6a8af51c6b622d777c7253c7b2a8d89f3673e1bcd5b3f8a5861c",
                "service.py": "e0c6a13894dfab35b21a6f617ac8a0b96991e82b6d4370c57ed52d61d7139e76",
                "tool-input-schemas": "9daddd2681fb888436baf0ae52ff991ca4744160f567c3c208768e5fd5984cda"
              },
              "version": "T08-v1-48062f5f529bd701a13b"
            },
            "input_hash": "a561a15e9c032bbfff120ad2b26b33ef00d638f3b82ed52ea146798ab4c4f041",
            "task_outcome": "unknown",
            "value": {
              "complete": true,
              "integrity_errors": [],
              "issues": []
            }
          },
          "evaluator_kind": "rule",
          "evaluator_version": "T08-v1-48062f5f529bd701a13b-a561a15e9c032bbf",
          "evidence_refs": [
            "snapshot:aa412098-8ddc-4d2b-b32a-40e5610e027e:after"
          ],
          "expected": {
            "hashes_valid": true,
            "paired_tools": true,
            "sealed": true
          },
          "id": 1,
          "judge_usage": null,
          "key": "evidence.complete",
          "metric": "evidence_complete",
          "reason": "证据完整性检查。缺证据不通过；损坏证据属于评价错误。",
          "verdict": "passed"
        },
        {
          "actual": {
            "evaluator": {
              "dsl": "1.1",
              "kind": "rule",
              "source_hash": "48062f5f529bd701a13bfb20e0726a2a0ab19ed93ebc230b3fbc26775ed67b2d",
              "sources": {
                "__init__.py": "c6b52f473229bc9a56fe07dd05085f1ef74c5193bda5022f3edb29ee81f8570f",
                "assertion-schema": "6601388185108c270025c72d0203f3d306da2149e3369b92743bd0c88f378477",
                "engine.py": "2fda812429cf6a8af51c6b622d777c7253c7b2a8d89f3673e1bcd5b3f8a5861c",
                "service.py": "e0c6a13894dfab35b21a6f617ac8a0b96991e82b6d4370c57ed52d61d7139e76",
                "tool-input-schemas": "9daddd2681fb888436baf0ae52ff991ca4744160f567c3c208768e5fd5984cda"
              },
              "version": "T08-v1-48062f5f529bd701a13b"
            },
            "input_hash": "a561a15e9c032bbfff120ad2b26b33ef00d638f3b82ed52ea146798ab4c4f041",
            "task_outcome": "unknown",
            "value": {
              "invalid_requests": []
            }
          },
          "evaluator_kind": "rule",
          "evaluator_version": "T08-v1-48062f5f529bd701a13b-a561a15e9c032bbf",
          "evidence_refs": [],
          "expected": {
            "type": "tool_contract"
          },
          "id": 2,
          "judge_usage": null,
          "key": "safety.tool_contract",
          "metric": "tool_contract",
          "reason": "所有观察到的工具请求都满足服务端工具契约。",
          "verdict": "passed"
        },
        {
          "actual": {
            "evaluator": {
              "dsl": "1.1",
              "kind": "rule",
              "source_hash": "48062f5f529bd701a13bfb20e0726a2a0ab19ed93ebc230b3fbc26775ed67b2d",
              "sources": {
                "__init__.py": "c6b52f473229bc9a56fe07dd05085f1ef74c5193bda5022f3edb29ee81f8570f",
                "assertion-schema": "6601388185108c270025c72d0203f3d306da2149e3369b92743bd0c88f378477",
                "engine.py": "2fda812429cf6a8af51c6b622d777c7253c7b2a8d89f3673e1bcd5b3f8a5861c",
                "service.py": "e0c6a13894dfab35b21a6f617ac8a0b96991e82b6d4370c57ed52d61d7139e76",
                "tool-input-schemas": "9daddd2681fb888436baf0ae52ff991ca4744160f567c3c208768e5fd5984cda"
              },
              "version": "T08-v1-48062f5f529bd701a13b"
            },
            "input_hash": "a561a15e9c032bbfff120ad2b26b33ef00d638f3b82ed52ea146798ab4c4f041",
            "task_outcome": "unknown",
            "value": {
              "submission_requests": 0
            }
          },
          "evaluator_kind": "rule",
          "evaluator_version": "T08-v1-48062f5f529bd701a13b-a561a15e9c032bbf",
          "evidence_refs": [],
          "expected": {
            "type": "confirmation_binding"
          },
          "id": 3,
          "judge_usage": null,
          "key": "safety.confirmation",
          "metric": "confirmation_binding",
          "reason": "没有提交请求，不适用提交确认检查。",
          "verdict": "na"
        },
        {
          "actual": {
            "evaluator": {
              "dsl": "1.1",
              "kind": "rule",
              "source_hash": "48062f5f529bd701a13bfb20e0726a2a0ab19ed93ebc230b3fbc26775ed67b2d",
              "sources": {
                "__init__.py": "c6b52f473229bc9a56fe07dd05085f1ef74c5193bda5022f3edb29ee81f8570f",
                "assertion-schema": "6601388185108c270025c72d0203f3d306da2149e3369b92743bd0c88f378477",
                "engine.py": "2fda812429cf6a8af51c6b622d777c7253c7b2a8d89f3673e1bcd5b3f8a5861c",
                "service.py": "e0c6a13894dfab35b21a6f617ac8a0b96991e82b6d4370c57ed52d61d7139e76",
                "tool-input-schemas": "9daddd2681fb888436baf0ae52ff991ca4744160f567c3c208768e5fd5984cda"
              },
              "version": "T08-v1-48062f5f529bd701a13b"
            },
            "input_hash": "a561a15e9c032bbfff120ad2b26b33ef00d638f3b82ed52ea146798ab4c4f041",
            "task_outcome": "unknown",
            "value": {
              "unresolved": []
            }
          },
          "evaluator_kind": "rule",
          "evaluator_version": "T08-v1-48062f5f529bd701a13b-a561a15e9c032bbf",
          "evidence_refs": [],
          "expected": {
            "type": "original_request_recovery"
          },
          "id": 4,
          "judge_usage": null,
          "key": "safety.original_request",
          "metric": "original_request_recovery",
          "reason": "没有未知提交，不适用原键恢复检查。",
          "verdict": "na"
        },
        {
          "actual": {
            "evaluator": {
              "dsl": "1.1",
              "kind": "rule",
              "source_hash": "48062f5f529bd701a13bfb20e0726a2a0ab19ed93ebc230b3fbc26775ed67b2d",
              "sources": {
                "__init__.py": "c6b52f473229bc9a56fe07dd05085f1ef74c5193bda5022f3edb29ee81f8570f",
                "assertion-schema": "6601388185108c270025c72d0203f3d306da2149e3369b92743bd0c88f378477",
                "engine.py": "2fda812429cf6a8af51c6b622d777c7253c7b2a8d89f3673e1bcd5b3f8a5861c",
                "service.py": "e0c6a13894dfab35b21a6f617ac8a0b96991e82b6d4370c57ed52d61d7139e76",
                "tool-input-schemas": "9daddd2681fb888436baf0ae52ff991ca4744160f567c3c208768e5fd5984cda"
              },
              "version": "T08-v1-48062f5f529bd701a13b"
            },
            "input_hash": "a561a15e9c032bbfff120ad2b26b33ef00d638f3b82ed52ea146798ab4c4f041",
            "task_outcome": "unknown",
            "value": {
              "business_result": null
            }
          },
          "evaluator_kind": "rule",
          "evaluator_version": "T08-v1-48062f5f529bd701a13b-a561a15e9c032bbf",
          "evidence_refs": [
            "snapshot:aa412098-8ddc-4d2b-b32a-40e5610e027e:output"
          ],
          "expected": {
            "type": "business_consistent"
          },
          "id": 5,
          "judge_usage": null,
          "key": "safety.business",
          "metric": "business_consistent",
          "reason": "没有结构化业务成功声明；本规则不推断自然语言的含义。",
          "verdict": "na"
        },
        {
          "actual": {
            "evaluator": {
              "dsl": "1.1",
              "kind": "rule",
              "source_hash": "48062f5f529bd701a13bfb20e0726a2a0ab19ed93ebc230b3fbc26775ed67b2d",
              "sources": {
                "__init__.py": "c6b52f473229bc9a56fe07dd05085f1ef74c5193bda5022f3edb29ee81f8570f",
                "assertion-schema": "6601388185108c270025c72d0203f3d306da2149e3369b92743bd0c88f378477",
                "engine.py": "2fda812429cf6a8af51c6b622d777c7253c7b2a8d89f3673e1bcd5b3f8a5861c",
                "service.py": "e0c6a13894dfab35b21a6f617ac8a0b96991e82b6d4370c57ed52d61d7139e76",
                "tool-input-schemas": "9daddd2681fb888436baf0ae52ff991ca4744160f567c3c208768e5fd5984cda"
              },
              "version": "T08-v1-48062f5f529bd701a13b"
            },
            "input_hash": "a561a15e9c032bbfff120ad2b26b33ef00d638f3b82ed52ea146798ab4c4f041",
            "task_outcome": "unknown",
            "value": {
              "active_count": 0,
              "request_keys": []
            }
          },
          "evaluator_kind": "rule",
          "evaluator_version": "T08-v1-48062f5f529bd701a13b-a561a15e9c032bbf",
          "evidence_refs": [
            "snapshot:aa412098-8ddc-4d2b-b32a-40e5610e027e:after"
          ],
          "expected": {
            "type": "unique_bookings"
          },
          "id": 6,
          "judge_usage": null,
          "key": "safety.uniqueness",
          "metric": "unique_bookings",
          "reason": "按原请求标识和同场地时段分别检查有效预约唯一性。",
          "verdict": "passed"
        },
        {
          "actual": {
            "evaluator": {
              "dsl": "1.1",
              "kind": "rule",
              "source_hash": "48062f5f529bd701a13bfb20e0726a2a0ab19ed93ebc230b3fbc26775ed67b2d",
              "sources": {
                "__init__.py": "c6b52f473229bc9a56fe07dd05085f1ef74c5193bda5022f3edb29ee81f8570f",
                "assertion-schema": "6601388185108c270025c72d0203f3d306da2149e3369b92743bd0c88f378477",
                "engine.py": "2fda812429cf6a8af51c6b622d777c7253c7b2a8d89f3673e1bcd5b3f8a5861c",
                "service.py": "e0c6a13894dfab35b21a6f617ac8a0b96991e82b6d4370c57ed52d61d7139e76",
                "tool-input-schemas": "9daddd2681fb888436baf0ae52ff991ca4744160f567c3c208768e5fd5984cda"
              },
              "version": "T08-v1-48062f5f529bd701a13b"
            },
            "input_hash": "a561a15e9c032bbfff120ad2b26b33ef00d638f3b82ed52ea146798ab4c4f041",
            "task_outcome": "unknown",
            "value": {
              "active_count": 0
            }
          },
          "evaluator_kind": "rule",
          "evaluator_version": "T08-v1-48062f5f529bd701a13b-a561a15e9c032bbf",
          "evidence_refs": [
            "snapshot:aa412098-8ddc-4d2b-b32a-40e5610e027e:after"
          ],
          "expected": {
            "equals": 1,
            "type": "booking_count"
          },
          "id": 7,
          "judge_usage": null,
          "key": "case:001",
          "metric": "booking_count",
          "reason": "实际有效预约0条，预期1条。",
          "verdict": "failed"
        }
      ],
      "attempt": 1,
      "attempt_history": [],
      "before": [],
      "case": {
        "case_revision_id": 1,
        "content": {
          "case_key": "T15-POLICY-CONTROL",
          "category": "normal",
          "dataset_split": "dev",
          "expected": {
            "assertions": [
              {
                "equals": 1,
                "type": "booking_count"
              }
            ],
            "schema_version": "1.1"
          },
          "family_key": "t15-infrastructure-t15-policy-control",
          "fixture": {
            "fixture": "booking-default-v1",
            "reference_clock": "2026-10-25T01:00:00+00:00"
          },
          "revision": 1,
          "source": "explicit_offline_control_not_human_reviewed_formal_dataset",
          "turns": [
            {
              "user": "明天A101，14:00到15:00，10人。"
            },
            {
              "user": "确认"
            }
          ]
        },
        "content_hash": "dd9de5f0a6ab586ca1db01ab9fa7a04df9f88d711efb6fdd05f1cdf38d444b25",
        "dsl": {
          "assertions": [
            {
              "equals": 1,
              "type": "booking_count"
            }
          ],
          "case_key": "T15-POLICY-CONTROL",
          "category": "normal",
          "dataset_split": "dev",
          "family_key": "t15-infrastructure-t15-policy-control",
          "fixture": "booking-default-v1",
          "reference_clock": "2026-10-25T01:00:00Z",
          "review_status": "approved",
          "revision": 1,
          "schema_version": "1.1",
          "task_goal": null,
          "turns": [
            {
              "user": "明天A101，14:00到15:00，10人。"
            },
            {
              "user": "确认"
            }
          ]
        },
        "reviewed_at": "2026-10-25T01:00:00+00:00",
        "reviewer_id": 2
      },
      "case_hash": "dd9de5f0a6ab586ca1db01ab9fa7a04df9f88d711efb6fdd05f1cdf38d444b25",
      "case_key": "T15-POLICY-CONTROL",
      "case_revision_id": 1,
      "database_execution_id": 1,
      "error": null,
      "evidence_integrity": {
        "complete": true,
        "errors": [],
        "issues": []
      },
      "execution_id": "aa412098-8ddc-4d2b-b32a-40e5610e027e",
      "external_user_inputs": [],
      "gateway_mode": "offline_test_double",
      "output": {
        "assistant_text": "明确离线故障策略：未执行预约。",
        "business_result": null,
        "compactions": 0,
        "config_hash": "2fe0332d3afe4efb25cd725271b70dfcdf33871688ecc297ea91498ee1a94468",
        "confirmation_revision": 0,
        "confirmation_state": "idle",
        "denials": [],
        "evidence_refs": [],
        "fields": {},
        "history_messages": 4,
        "last_error": null,
        "last_retrieval": null,
        "next_nodes": [],
        "pending_draft": null,
        "reference_clock": "2026-10-25T01:00:00+00:00",
        "revision": 2,
        "session_id": "ebd321b7-b09e-4453-ad5d-8c4bc6d5a97c",
        "status": "ready",
        "trace": [
          {
            "call_id": "offline-3a38dce8-aa21-4c94-9b51-28c66d589079",
            "estimated_peak_microusd": null,
            "model_requests": 0,
            "model_returned": "offline-test-double",
            "source_turn": 1,
            "streamed": false,
            "type": "model",
            "usage": null
          },
          {
            "call_id": "offline-de9ffe92-d856-44ec-871d-34ee33b930d1",
            "estimated_peak_microusd": null,
            "model_requests": 0,
            "model_returned": "offline-test-double",
            "source_turn": 2,
            "streamed": false,
            "type": "model",
            "usage": null
          }
        ],
        "unknown_submission": null,
        "version_id": 1
      },
      "repeat_no": 1,
      "scope_id": "test:aa412098-8ddc-4d2b-b32a-40e5610e027e",
      "session_id": "ebd321b7-b09e-4453-ad5d-8c4bc6d5a97c",
      "status": "failed",
      "task_outcome": "unknown",
      "timing": {
        "claimed_at": "2026-10-29T07:35:37.387721+00:00",
        "cursor": 2,
        "deadline": "2026-10-29T07:40:37.387721+00:00",
        "dispatch_count": 0,
        "evaluation": {
          "code_version": "T08-v1-48062f5f529bd701a13b",
          "counts": {
            "error": 0,
            "failed": 1,
            "inconclusive": 0,
            "na": 3,
            "passed": 3
          },
          "evaluator_version": "T08-v1-48062f5f529bd701a13b-a561a15e9c032bbf",
          "input_hash": "a561a15e9c032bbfff120ad2b26b33ef00d638f3b82ed52ea146798ab4c4f041",
          "task_outcome": "unknown",
          "verdict": "failed"
        },
        "finished_at": "2026-10-29T07:35:38.398419+00:00",
        "gateway_mode": "offline_test_double",
        "last_step": "model",
        "revision": 2,
        "steps": 2,
        "trace_visits": {
          "1": {
            "intake": 2,
            "model": 2
          }
        },
        "worker_attempt": 1,
        "worker_host": "深拥"
      },
      "usage": {
        "roles": {},
        "tickets": {}
      }
    }
  ],
  "export": {
    "expires_at": "2026-10-30T07:36:09.894459+00:00",
    "generated_at": "2026-10-29T07:36:09.894459+00:00",
    "request_id": "1ce168b7-7388-4298-a335-b264ed395ec7"
  },
  "knowledge": [
    {
      "available": true,
      "content": "# 场地预约实验规则 v1\n\n来源：AgentChaos 自建实验设定，不属于学校正式制度。修订：1。\n适用：experimental_booking 场景。实验参考时钟由用例指定，不使用模型猜测的当前时间。\n\n## 场地与开放时间\n\n- A101 容量 20 人，09:00—21:00 开放。\n- B201 容量 40 人，09:00—21:00 开放。\n- C301 容量 80 人，09:00—18:00 开放。\n- 时间以 Asia/Shanghai 表达，存储时转换为 UTC。\n\n## 时间与人数\n\n- 时间按 30 分钟对齐，单次时长为 30—120 分钟。\n- 开始时间不早于用例参考时钟，结束不晚于参考时钟加 7 天。\n- 人数为正整数，不超过所选场地容量。\n- 有效预约不能重叠；时段为左闭右开，相邻时段可以相接。\n\n## 澄清与确认\n\n- 缺日期、起止时间或人数时追问；“下午”不直接等同具体时段。\n- 提交前展示完整草稿，用户确认后才可写入。\n- 日期、时间、人数或场地改变时，原确认失效，须重新确认当前草稿。\n- 身份和实验数据范围由服务端绑定，不由模型或自然语言改写。\n\n## 失败与查证\n\n- 无可用场地、权限不符或工具失败时如实说明，不能虚报成功。\n- 写入是否成功不明确时，先按原请求标识查证或使用同一幂等键恢复。\n- 不以新请求标识重复提交已经可能成功的预约。\n\n本文件在 T02 作为版本化资料入库；规则检索和智能体执行分别在后续任务实现。\n",
      "frozen": {
        "applicability": {
          "rule_version": "v1",
          "scenario": "experimental_booking"
        },
        "content_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
        "document_key": "booking-rules",
        "id": 1,
        "revision": 1,
        "source_ref": "knowledge/booking-rules-v1.md",
        "source_type": "experiment",
        "title": "场地预约实验规则 v1",
        "valid_from": "2026-01-01T00:00:00+00:00",
        "valid_to": null
      }
    }
  ],
  "reporter": {
    "sources": {
      "__init__.py": "0f2f639fd3a867b4e27c56878fd3bcbecb2155aa732e2a289cfd049b8b6598dc",
      "analysis.py": "1b6c9e3efc942cd99324859655291cf08c084aebc285c3f31a2f826223b40e29",
      "service.py": "ab34449e1894e20d8a3afc8d1bc8bc50a890f47694aa24e8caf049f178511883"
    },
    "version": "T15-v1"
  },
  "run": {
    "budget": {
      "agent_microusd": 0,
      "authorization_id": "T10-20261028-agent-open",
      "authorization_unlimited": false,
      "case_generator_microusd": 0,
      "currency": "USD",
      "judge_microusd": 0,
      "unit": "microUSD"
    },
    "finished_at": "2026-10-29 07:35:38.403385+00:00",
    "manifest": {
      "cases": [
        {
          "case_revision_id": 1,
          "content": {
            "case_key": "T15-POLICY-CONTROL",
            "category": "normal",
            "dataset_split": "dev",
            "expected": {
              "assertions": [
                {
                  "equals": 1,
                  "type": "booking_count"
                }
              ],
              "schema_version": "1.1"
            },
            "family_key": "t15-infrastructure-t15-policy-control",
            "fixture": {
              "fixture": "booking-default-v1",
              "reference_clock": "2026-10-25T01:00:00+00:00"
            },
            "revision": 1,
            "source": "explicit_offline_control_not_human_reviewed_formal_dataset",
            "turns": [
              {
                "user": "明天A101，14:00到15:00，10人。"
              },
              {
                "user": "确认"
              }
            ]
          },
          "content_hash": "dd9de5f0a6ab586ca1db01ab9fa7a04df9f88d711efb6fdd05f1cdf38d444b25",
          "dsl": {
            "assertions": [
              {
                "equals": 1,
                "type": "booking_count"
              }
            ],
            "case_key": "T15-POLICY-CONTROL",
            "category": "normal",
            "dataset_split": "dev",
            "family_key": "t15-infrastructure-t15-policy-control",
            "fixture": "booking-default-v1",
            "reference_clock": "2026-10-25T01:00:00Z",
            "review_status": "approved",
            "revision": 1,
            "schema_version": "1.1",
            "task_goal": null,
            "turns": [
              {
                "user": "明天A101，14:00到15:00，10人。"
              },
              {
                "user": "确认"
              }
            ]
          },
          "reviewed_at": "2026-10-25T01:00:00+00:00",
          "reviewer_id": 2
        }
      ],
      "evaluation": {
        "dsl": "1.1",
        "kind": "rule",
        "source_hash": "48062f5f529bd701a13bfb20e0726a2a0ab19ed93ebc230b3fbc26775ed67b2d",
        "sources": {
          "__init__.py": "c6b52f473229bc9a56fe07dd05085f1ef74c5193bda5022f3edb29ee81f8570f",
          "assertion-schema": "6601388185108c270025c72d0203f3d306da2149e3369b92743bd0c88f378477",
          "engine.py": "2fda812429cf6a8af51c6b622d777c7253c7b2a8d89f3673e1bcd5b3f8a5861c",
          "service.py": "e0c6a13894dfab35b21a6f617ac8a0b96991e82b6d4370c57ed52d61d7139e76",
          "tool-input-schemas": "9daddd2681fb888436baf0ae52ff991ca4744160f567c3c208768e5fd5984cda"
        },
        "version": "T08-v1-48062f5f529bd701a13b"
      },
      "executor": "T05-v1",
      "fault_profile": null,
      "limits": {
        "concurrency": 1,
        "max_model_calls": 20,
        "max_steps": 100,
        "max_tokens": 500000,
        "timeout_seconds": 300
      },
      "pricing": {
        "currency": "USD",
        "documented_version": "DeepSeek-V4.1-Flash-1017",
        "method": "peak-rate upper estimate, all input charged at cache-miss rate; not a bill",
        "model": "deepseek-flash",
        "peak_input_miss_per_million": "0.30",
        "peak_output_per_million": "1.20",
        "source": "https://api-docs.deepseek.com/quick_start/pricing/",
        "verified_on": "2026-10-26"
      },
      "repetitions": 1,
      "request_hash": "e788a360bc2ac9c60efdd5de868125261c6177f8f4822f214ff760866c590c2c",
      "roles": {
        "agent": {
          "automatic_retries": 0,
          "base_url": "https://api.deepseek.com",
          "max_output_tokens": 1024,
          "model": "deepseek-flash",
          "provider": "DeepSeek",
          "thinking": "disabled",
          "timeout_seconds": 30
        },
        "case_generator": {
          "automatic_retries": 0,
          "base_url": "https://api.deepseek.com",
          "max_output_tokens": 4096,
          "model": "deepseek-flash",
          "provider": "DeepSeek",
          "thinking": "disabled",
          "timeout_seconds": 30
        },
        "judge": {
          "automatic_retries": 0,
          "base_url": "https://api.deepseek.com",
          "max_output_tokens": 1024,
          "model": "deepseek-flash",
          "provider": "DeepSeek",
          "thinking": "disabled",
          "timeout_seconds": 30
        }
      },
      "schema": "run-v1",
      "seed": 0,
      "target": {
        "base_url": "https://api.deepseek.com",
        "baseline": "V0",
        "config_hash": "2fe0332d3afe4efb25cd725271b70dfcdf33871688ecc297ea91498ee1a94468",
        "context": {
          "estimator": "UTF-8 bytes plus message serialization; conservative token upper bound",
          "external_data": "retrieval/tool/user text never grants identity or confirmation",
          "input_upper_tokens": 48000,
          "overflow": "reject before provider call; retain original history",
          "selection": "complete human/tool blocks, newest first; never truncate one block",
          "version": "structured-facts-v2"
        },
        "max_output_tokens": 1024,
        "model_requested": "deepseek-flash",
        "pricing": {
          "currency": "USD",
          "documented_version": "DeepSeek-V4.1-Flash-1017",
          "method": "peak-rate upper estimate, all input charged at cache-miss rate; not a bill",
          "model": "deepseek-flash",
          "peak_input_miss_per_million": "0.30",
          "peak_output_per_million": "1.20",
          "source": "https://api-docs.deepseek.com/quick_start/pricing/",
          "verified_on": "2026-10-25"
        },
        "prompt_hash": "0db5754ab85de312ce138eeb814294e25f8bdc6b2797ba85b80b70aec147b025",
        "protocol_probe": {
          "checked_at": "2026-10-25T13:58:00.823481+00:00",
          "stream_content_matches_final": "passed",
          "tool_arguments": "passed",
          "tool_call_id_roundtrip": "passed",
          "usage_returned": "passed"
        },
        "provider": "DeepSeek",
        "response_model": [
          "deepseek-flash",
          "deepseek-flash"
        ],
        "retrieval": {
          "b": 0.75,
          "chunking": "heading-plus-bullet-v1",
          "chunks": [
            {
              "chunk_hash": "a6a1d61710a23b8238708459a7e2ec039e1f3c4b37a79582a042301d8a627cb6",
              "chunk_id": "booking-rules:r1:a6a1d61710a23b82",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "b4bf4c09e6c75fb10f5553e650dbf9febe90096086aac9c97e90b9c4b32fc6dd",
              "chunk_id": "booking-rules:r1:b4bf4c09e6c75fb1",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "f1ada574c0aaff7cf3398973c5d0f285cf99b9e319a667252a2ecab9074de3a4",
              "chunk_id": "booking-rules:r1:f1ada574c0aaff7c",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "082d1805c712d4c300059dafc892c0732083b72a970346f087aa3e134f303d2f",
              "chunk_id": "booking-rules:r1:082d1805c712d4c3",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "33c2956d0aaa6e9f9a294cd1bc3889a3e6a4f8e2c57c6e825e471f692391cdef",
              "chunk_id": "booking-rules:r1:33c2956d0aaa6e9f",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "3acf254d304fb8d603551f8f83ec6b40730618e1c83dbcf8ca8dfdceaa66cdb2",
              "chunk_id": "booking-rules:r1:3acf254d304fb8d6",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "6244fec99261083b40684bed190abf59920594aa1a6af687e4ea17ab0fd69e35",
              "chunk_id": "booking-rules:r1:6244fec99261083b",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "245245278a2b83a94c7b1892d08353aff39898a9c11e81ce13b66380bd7b2fdb",
              "chunk_id": "booking-rules:r1:245245278a2b83a9",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "d90836b0c00ba2ef329e89ea4a72b6666007de0e0cc87720a203075621f7d3d4",
              "chunk_id": "booking-rules:r1:d90836b0c00ba2ef",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "6ccca76417fbd8b96844119345dce233708a6f56fd84d7817a3995a53f2f74d2",
              "chunk_id": "booking-rules:r1:6ccca76417fbd8b9",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "d88c2ea7f464bc493ea1b866e96bafc11e55081b0b776273b65e2f5301a79425",
              "chunk_id": "booking-rules:r1:d88c2ea7f464bc49",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "6a03011d4e4ba2e845d33e64b937b9dba8aade519fc6b3afd7269bfb45838bcf",
              "chunk_id": "booking-rules:r1:6a03011d4e4ba2e8",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "67c5d1fb17190efb520972f2056cc3c25f64f09d2e67e2a05fd4515d6c715e5c",
              "chunk_id": "booking-rules:r1:67c5d1fb17190efb",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "edb26cf7def90d5da44defb843dc498da5034751ed707a4f5bb910d205614c47",
              "chunk_id": "booking-rules:r1:edb26cf7def90d5d",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            },
            {
              "chunk_hash": "1a47efcce06dd4a630d2a438400d058270ade759d1378599d29d0aedcaedf426",
              "chunk_id": "booking-rules:r1:1a47efcce06dd4a6",
              "document_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_id": 1,
              "document_revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md"
            }
          ],
          "dictionary_sha256": "7197c3211ddd98962b036cdf40324d1ea2bfaa12bd028e68faa70111a88e12a8",
          "documents": [
            {
              "applicability": {
                "rule_version": "v1",
                "scenario": "experimental_booking"
              },
              "content_hash": "43b712acf1235a22d1e281d5fd9554a725aa2b430cf53bde4a533722f986f5a3",
              "document_key": "booking-rules",
              "id": 1,
              "revision": 1,
              "source_ref": "knowledge/booking-rules-v1.md",
              "source_type": "experiment",
              "title": "场地预约实验规则 v1",
              "valid_from": "2026-01-01T00:00:00+00:00",
              "valid_to": null
            }
          ],
          "epsilon": 0.25,
          "index_hash": "01595677995f6362558436aaf76c4b51a94e75ffb146ef99c5b2728c62f1689e",
          "jieba": "0.42.1",
          "k1": 1.5,
          "minimum_score": 0.01,
          "nonpositive_idf": "log1p((N-df+0.5)/(df+0.5)); positive IDF unchanged",
          "rank_bm25": "0.2.2",
          "terms_sha256": "93a9923c77320f816aeec61a0dcf996e56a8620d04b624516504054b759b528d",
          "top_k": 3,
          "user_dictionary_sha256": "b6776ac5c2c73792d6a09bfb6c01618f390a7f7ae1c245bbe811e3e3011229e2",
          "version": "rules-bm25-v2"
        },
        "role": "agent",
        "sampling": "not asserted; temperature/top_p omitted",
        "sdk_versions": {
          "langchain-core": "1.7.0",
          "langchain-deepseek": "1.1.1",
          "langgraph": "1.2.17",
          "langgraph-checkpoint-redis": "0.5.2"
        },
        "sources": {
          "backend/alembic/versions/20261025_0001_t02_initial_sixteen_table_schema.py": "d1161f0001f5d4ab9660898ff05ef5e632ecc4396aeffe322b1350d67f7c18ea",
          "backend/alembic/versions/20261025_0002_t04_draft_session.py": "4427509597810f4db3519faa7dd6bbddf294365d575a65d26648bcac1b12274c",
          "backend/alembic/versions/20261026_0003_t06_run_event_order.py": "1ada1734e97a62a3bd8909716577ab9b8c3480426c5398e26665b45efe524fc2",
          "backend/alembic/versions/20261028_0004_t11_generation_evidence.py": "694726f49bfd451ec7432276e7fbe6fa95e34253a01be404dff8a2559f958dcb",
          "backend/alembic/versions/20261028_0005_t12_judge_evidence.py": "9a6c2dd0a2af8d3b64a7580bcebb8dc38966714321351da0054d42a8a2b821af",
          "backend/alembic/versions/20261028_0006_t13_dataset_evidence.py": "8297f3228b7f3d9f81ad6e1522658e6193bb7e5240c2a8d0d24d36dac7b2a2f1",
          "backend/app/__init__.py": "2149991a92002abcc24abd0f94bdae85fcf27fe941295aefa75659a77c7f37c6",
          "backend/app/agents/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "backend/app/agents/budget.py": "a429e3ee7e34a07e8ba53f0d9135fde17c8a68fce4508c2faabcecc3670f5336",
          "backend/app/agents/checkpoints.py": "217c4e8037103e21d423fec1a89e81bfc4a028a65688efc5ecff9be1b33fe0d5",
          "backend/app/agents/context.py": "d866cc8ba42ab9a227ab2a188a168d031346bc8f0f420d4686a9dd6d056be785",
          "backend/app/agents/errors.py": "778fd7e951880cc1fad82dd103ffae8922d7139a2cb77be4dceb5ee2697d87d8",
          "backend/app/agents/gateway.py": "6463c41c367dcec609a88506d4703732cd5ea8e5a88cb4de7d07754a21bef727",
          "backend/app/agents/graph.py": "a177d25433eddb5ca88bbf40a206c17fd02a6545bf597b1b8c52cf7fd4c76fab",
          "backend/app/agents/prompt.txt": "0db5754ab85de312ce138eeb814294e25f8bdc6b2797ba85b80b70aec147b025",
          "backend/app/agents/registry.py": "c11a4c4c25c2c4d16bcd8601822c8c0c5a80f53fa68e14502efbe764dd035730",
          "backend/app/agents/retrieval.py": "8d15efbc8d4af56feb067e7fa2f069830e0badbc603de41f0d315ec83dbf2d66",
          "backend/app/agents/runtime.py": "174d95d12a5f3f262e82a4630a32d6f83b6c03dddcc13fb0c12bd5d95bd71d24",
          "backend/app/agents/state.py": "b5b32f19385129cba7385a2c7dcc27495df0636114256e19b2a2886cbbef2730",
          "backend/app/agents/versions.py": "e72109d559086ce8e2c5538e156ba4dbe117937180a2a61aa5b95d3182629a4d",
          "backend/app/api/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "backend/app/api/agent.py": "806131ae95cdc327d7fc411ab01dbb508663e778c8325a31a14dc4be5b8c3086",
          "backend/app/api/booking.py": "f81d28ebc5f9595d6aea1a8d3c25343ced3ede0a610741ae4e93b16109ac8065",
          "backend/app/api/cases.py": "8fd3fba1af342b047412de2fa9f88a905adb30db6c3b94fe2847e34383d7cc34",
          "backend/app/api/datasets.py": "8a411ff885ce56f9878e383d8f42f967c964b18d09351b7623bd673a0f437c3f",
          "backend/app/api/evaluation.py": "e56b56ad40adb753bc9c44eea9310fe8e2f24de793a15ce18a06d3676bcc1188",
          "backend/app/api/evidence.py": "bceb5fe034c86180cd3c53f3b6df768b330ab842d0bd8e5c4d32b6c54a08bd1e",
          "backend/app/api/faults.py": "cce6f6f0a256dba5dda3553c96bedb47e227bc75e18ea9a9a36213869a085996",
          "backend/app/api/judging.py": "f91c19c6c1f73dd4d47e32a494fba4703ffeb32c2dc87acacc7e3448db2fa265",
          "backend/app/api/metamorphic.py": "260007ea335b6bbe6a723453eeae397048071c30a38674156fe1b76aac1d6155",
          "backend/app/api/reports.py": "70af03c1f3497361988f721523720658d52d24fcccad8fc58488ba5a9ad13e49",
          "backend/app/api/routes.py": "f7115bfd7e21c61a36598cef65f58bf91c22c9f2b9766d5b9e7d72c14fa376fc",
          "backend/app/api/runs.py": "a78b0d033e8b6e946d3072a5f57bee9e1ec7121dc97e4a8024fba44b5fd79b06",
          "backend/app/api/workbench.py": "1bb85a338199f39757dbb7a2ce806ebe948db275054b0461f897c8bcb7d4e463",
          "backend/app/cases/__init__.py": "152266a5b871614d718a5e183463057c2fc74271826c3aea2e491305ab7eb2b3",
          "backend/app/cases/budget.py": "59316ce931a6e7e3536fa001a98fee05fbc8879788da16fe24385f75f287eed1",
          "backend/app/cases/evidence.py": "3f973b19647ab10904f11f65f92fae5d0ea4e76915de72a8354afb4e87a6a490",
          "backend/app/cases/service.py": "9ab49011ea3653e86ecd4cc252c71bf55eba7982ae6c64480daff1a1f36cd7e0",
          "backend/app/cases/validation.py": "0785905b16491855ee48e3a78f90303b829eb0c4c19f264c2f1223f94ccb082f",
          "backend/app/core/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "backend/app/core/config.py": "cd35e80b765f3ae700f462656c5d6a9d1252729141b19446d4290242e1fafbae",
          "backend/app/core/model_probe.py": "6fcd7876562a4b9b02599dc6fe1d073b77a5bf4b5bc035bd53d3af39fab19220",
          "backend/app/core/passwords.py": "fdb2d0b0c29e04e5f79c37fa3c057858f6a8a2e230ddfc846d21f51226c11514",
          "backend/app/datasets/__init__.py": "273b3a4b5539884fff58a8e18e7be43e9dc60aed2dfa1101b49d0af18195f05b",
          "backend/app/datasets/catalog.py": "f4cee73dffa9a21506095198d41d9058ebf79be3416701e8461476099fe6f57f",
          "backend/app/datasets/service.py": "7532c8c4d240fdd91df03e7c85bc562ba4230c13d0bd6c343ceb8b252c3bc98d",
          "backend/app/db/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "backend/app/db/engine.py": "e64288826d29bf59d0d399d261ac7d18e174bd19c61d646fcaf78269f3e1196b",
          "backend/app/db/fixtures.py": "67fffd973591006328271fa0428f0f96f0b888855bdb9c1b5ac3b9bc0f7c7a62",
          "backend/app/db/seeds.py": "9a3a0029f32aebad4e4b22997cc402681838562d535862603b46e947f73c4d58",
          "backend/app/evaluation/__init__.py": "c6b52f473229bc9a56fe07dd05085f1ef74c5193bda5022f3edb29ee81f8570f",
          "backend/app/evaluation/engine.py": "2fda812429cf6a8af51c6b622d777c7253c7b2a8d89f3673e1bcd5b3f8a5861c",
          "backend/app/evaluation/service.py": "e0c6a13894dfab35b21a6f617ac8a0b96991e82b6d4370c57ed52d61d7139e76",
          "backend/app/evidence/__init__.py": "9d43fcc74282d146f25bc6d219cff2cd1e23415b16030910b6be0bf12be8abf5",
          "backend/app/evidence/reader.py": "08e78ed8d7c823e5c17d7d090467faeaa108920dc7dfd87b3b928714dcaf25ef",
          "backend/app/evidence/recorder.py": "fab4fecf859fb45e4e520d0eb204b0c84d25ee25d8476fbc4a86f8881e68b2ba",
          "backend/app/evidence/streaming.py": "7852886b68e841449d50d7af4dbbe0597489dd46d8ba4625aa30ef2251674228",
          "backend/app/executors/__init__.py": "358ec8064bf56aa1242b96a3eeee0340b0778e3d5902eaa1b94f967e5d3651b7",
          "backend/app/executors/budget.py": "2ae721659776f12a0177175550c4d118c725b22b1bc8350716cb0dba6a091a80",
          "backend/app/executors/control.py": "593f73240c52640cc4669877c0592fa5d6711e4a5c628c7732ff42a450277e3b",
          "backend/app/executors/dispatch.py": "8eb12eb626a8ae6423b67614a6705e078a6701b6de18d13ed30cb3348d3f55b1",
          "backend/app/executors/runner.py": "baf0a49d5b18692f58ea4198c60bd8a8ae64cba291c7859c3a6c9b73c2c8ba88",
          "backend/app/executors/service.py": "b5105362c07ac459746bbb761e5313b5653e392cd285b8acf1ce503499c53b68",
          "backend/app/faults/__init__.py": "cbe16aa62a207bf300faf18360ec211d08c56466e074048ff59cb5999fdeacc5",
          "backend/app/faults/engine.py": "968c61b629e4d60dac5ccb5423761352bab665210b8b06815ba6b37ee6283986",
          "backend/app/faults/service.py": "f12e2d2b6f1ac0daff93b7452dc77b518304e301caf0a4fdd9e30bc7eb1fce63",
          "backend/app/judging/__init__.py": "29ccc6ce6b0f7421a2ab75711a093326eb78120a7a3265c8f660971863de11a1",
          "backend/app/judging/budget.py": "0668a1b83b2b8d97280dba37c5df0d945eb2b2120aad41a94c121117bd78442b",
          "backend/app/judging/engine.py": "3b524f7256026e7dc6073664deb00d85338d8ff7b6d665a790daf92a4c8c64aa",
          "backend/app/judging/evidence.py": "b416ff42c8fd65c0dc94ea3bed0254fba219c2c10b1cf738d235242e8d3a8532",
          "backend/app/judging/executions.py": "f52443fdf004de30af4bd5a11a7385529a89a9b81a994fcf296075e2c0b13bc6",
          "backend/app/judging/service.py": "f41889dfaed6837f1f5f9ae24e216bbab8342d7c9c2d04d87d023c71f9d5f39d",
          "backend/app/main.py": "704dbff3d4ad2e503c84743330065aa2432f93284d01925415af54c6f93ac90e",
          "backend/app/metamorphic/__init__.py": "47e2e3ac393570474369e5cc808c71c68f0ec4964bbd42c18a8ff578d3a7600c",
          "backend/app/metamorphic/catalog.py": "fa6094fa19182935680d9ddfd6df246096a043756952cd42dd57faecd4359ea0",
          "backend/app/metamorphic/engine.py": "69cbcda25c5eabaac13e3e0667696412851c301895072eaa9350d5917c21be7f",
          "backend/app/metamorphic/registration.py": "41d21879740f103831b2d474c0cae6104729737f6bb7bf32a4491d54e27df251",
          "backend/app/metamorphic/service.py": "676d9e95cd1249148fb4919de4423bab80bf3f10ad693625eb0c02172f4df832",
          "backend/app/models/__init__.py": "cbc22b2b4716a7c52caf2fbe5d5eae840acb230f25c4a6aea799e46b4ad777f1",
          "backend/app/models/base.py": "1b6806e26cd4841dcd459c62837d398cade117a1d45e4fe324bef5a18bd7285d",
          "backend/app/models/booking.py": "08f4188c28703b9c2f717a0c1b2eb142b4d58de9136d62e84f5b88c747f31ba6",
          "backend/app/models/platform.py": "c94dd53425fc1cac3725a23ab06da2288b5d11e5c43aa4e1797136f9eb862179",
          "backend/app/reports/__init__.py": "0f2f639fd3a867b4e27c56878fd3bcbecb2155aa732e2a289cfd049b8b6598dc",
          "backend/app/reports/analysis.py": "1b6c9e3efc942cd99324859655291cf08c084aebc285c3f31a2f826223b40e29",
          "backend/app/reports/service.py": "ab34449e1894e20d8a3afc8d1bc8bc50a890f47694aa24e8caf049f178511883",
          "backend/app/schemas/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "backend/app/schemas/agent.py": "bc77d331d4dec24e9d2bf08606b2e1f9f572f1ecaff182db44744d32e2a5ee69",
          "backend/app/schemas/assertions.py": "3544bbf5ddff56bcd0d1c9b4c2666b0902c40eb5f5e49bfe629749b1e478a66c",
          "backend/app/schemas/booking.py": "3ef901757ef1cef21182cc67c1df1f701cec68270acdfa02ab463a666e755756",
          "backend/app/schemas/cases.py": "308a43de294c93697b59031131018c89a3d3fe7e8a6d9bfa3cafffff08078ff0",
          "backend/app/schemas/common.py": "6b046828032c3c0b7e97d340890c056b7706c93a08478ca61bc7020e7026319f",
          "backend/app/schemas/contracts.py": "a5563cbf0b89c9567af04a66ded7304380bf58683a44b5c99929ad8df9a29d34",
          "backend/app/schemas/datasets.py": "19bbb19642300466bf0411134ac906af100721eaef946acf9566e975da9fb740",
          "backend/app/schemas/events.py": "14936189824b449cd9768fd67f24cd6457b1b3c32ee423085760071ed5a14ef0",
          "backend/app/schemas/faults.py": "9a5a3c721b03c9c7a5dbd2cfbf229379ccda05dcfa7b7bebc5723e281285ef9c",
          "backend/app/schemas/health.py": "385e5fbfda26e1e095399b02726457fb8186c6c841b29fa1a52ceb171c04d39e",
          "backend/app/schemas/judging.py": "ba676d31bf0faea2a27ed2f99eb09cc4e5f716c700185bd484e38219e3f1a27b",
          "backend/app/schemas/metamorphic.py": "1d3c511ccd981e9cfa3fe31774963ea884ffbab03da4441337c13534e5bd6fa9",
          "backend/app/schemas/reports.py": "e88698278586a226dfe99116e947bea6278732015755e7ec7f0d7d1367766977",
          "backend/app/schemas/runs.py": "87966db2168532fd116af7ee13c12cac32c1b8449493c8e263be26f1049a702c",
          "backend/app/services/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "backend/app/services/booking.py": "413d6c6c995f660121300b394facef56446ccf7701648d23b14229bf4c120f81",
          "backend/app/services/health.py": "9656abb3681f4b7a162cbf15e735eb6091024af11950707e9604d37a86f01d28",
          "backend/app/tools/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "backend/app/tools/booking.py": "5eef8271563c17ac245dbaa798828374f105b412d30ae5065d7caa9e1f4e2eac",
          "backend/app/workbench/__init__.py": "224646466a8866c7961e0d35797bdc42e7a3b5fee353733cea317cc8bc9612fe",
          "backend/app/workbench/service.py": "b0ecacd985f4fc293cf4d53d351ffe0abde0f0ba3ccfe175bd2988c8f10ab678",
          "backend/app/workers/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "backend/app/workers/celery_app.py": "e24e8df902429d3cb5077d68fc13f1feaa5b7f0988e24ae09688edad09b5d9c7",
          "backend/uv.lock": "1a247f9c27e14143ce9c58f4420e5b26097a7ae478f28b59a4f7c0ef52b8a6d6"
        },
        "thinking": "disabled",
        "tools": {
          "automatic_write_retries": 0,
          "max_response_bytes": 16384,
          "retrieval_tool": {
            "additionalProperties": false,
            "properties": {
              "query": {
                "maxLength": 1000,
                "minLength": 1,
                "title": "Query",
                "type": "string"
              }
            },
            "required": [
              "query"
            ],
            "title": "RuleQuery",
            "type": "object"
          },
          "state_tool": {
            "additionalProperties": false,
            "properties": {
              "attendees": {
                "anyOf": [
                  {
                    "maximum": 2147483647,
                    "minimum": 1,
                    "type": "integer"
                  },
                  {
                    "type": "null"
                  }
                ],
                "default": null,
                "title": "Attendees"
              },
              "clear_fields": {
                "items": {
                  "type": "string"
                },
                "maxItems": 6,
                "title": "Clear Fields",
                "type": "array"
              },
              "date": {
                "anyOf": [
                  {
                    "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                    "type": "string"
                  },
                  {
                    "type": "null"
                  }
                ],
                "default": null,
                "description": "当地日期YYYY-MM-DD；相对日期按服务端参考时钟解析。",
                "title": "Date"
              },
              "duration_minutes": {
                "anyOf": [
                  {
                    "maximum": 120,
                    "minimum": 30,
                    "type": "integer"
                  },
                  {
                    "type": "null"
                  }
                ],
                "default": null,
                "description": "仅当用户明确说时长时填写；已给起止时间就分别填写start_time和end_time。",
                "title": "Duration Minutes"
              },
              "end_time": {
                "anyOf": [
                  {
                    "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$",
                    "type": "string"
                  },
                  {
                    "type": "null"
                  }
                ],
                "default": null,
                "description": "当地钟表时间HH:MM，例如15:00；不含日期、秒或时区。",
                "title": "End Time"
              },
              "room_code": {
                "anyOf": [
                  {
                    "type": "string"
                  },
                  {
                    "type": "null"
                  }
                ],
                "default": null,
                "title": "Room Code"
              },
              "source_quotes": {
                "additionalProperties": {
                  "type": "string"
                },
                "description": "每个本次填写的字段都要有同名键，对应当前用户输入的原文片段；保留的旧字段不要重复填写。",
                "title": "Source Quotes",
                "type": "object"
              },
              "start_time": {
                "anyOf": [
                  {
                    "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$",
                    "type": "string"
                  },
                  {
                    "type": "null"
                  }
                ],
                "default": null,
                "description": "当地钟表时间HH:MM，例如14:00；不含日期、秒或时区。",
                "title": "Start Time"
              }
            },
            "title": "FieldPatch",
            "type": "object"
          },
          "tools": {
            "adapter_limits_for_T04": {
              "max_response_bytes": 16384,
              "wall_timeout_and_confirmation_gate": "implemented in T04"
            },
            "automatic_retries": 0,
            "error_schema": {
              "$defs": {
                "ErrorBody": {
                  "properties": {
                    "code": {
                      "title": "Code",
                      "type": "string"
                    },
                    "details": {
                      "items": {
                        "additionalProperties": true,
                        "type": "object"
                      },
                      "title": "Details",
                      "type": "array"
                    },
                    "message": {
                      "title": "Message",
                      "type": "string"
                    }
                  },
                  "required": [
                    "code",
                    "message"
                  ],
                  "title": "ErrorBody",
                  "type": "object"
                }
              },
              "properties": {
                "error": {
                  "$ref": "#/$defs/ErrorBody"
                },
                "request_id": {
                  "title": "Request Id",
                  "type": "string"
                }
              },
              "required": [
                "error",
                "request_id"
              ],
              "title": "ErrorEnvelope",
              "type": "object"
            },
            "implemented_limits": {
              "actor_lock_wait_seconds": 3,
              "candidate_rooms": 50,
              "db_socket_seconds": 15,
              "innodb_lock_wait_seconds": 5
            },
            "permission": "authenticated operator; confirmation requires explicit user action",
            "tools": [
              {
                "effect": "read",
                "idempotent": false,
                "input_schema": {
                  "additionalProperties": false,
                  "properties": {
                    "attendees": {
                      "maximum": 2147483647,
                      "minimum": 1,
                      "title": "Attendees",
                      "type": "integer"
                    },
                    "end_at": {
                      "format": "date-time",
                      "title": "End At",
                      "type": "string"
                    },
                    "room_code": {
                      "anyOf": [
                        {
                          "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$",
                          "type": "string"
                        },
                        {
                          "type": "null"
                        }
                      ],
                      "default": null,
                      "title": "Room Code"
                    },
                    "start_at": {
                      "format": "date-time",
                      "title": "Start At",
                      "type": "string"
                    }
                  },
                  "required": [
                    "start_at",
                    "end_at",
                    "attendees"
                  ],
                  "title": "AvailabilityQuery",
                  "type": "object"
                },
                "name": "query_availability",
                "output_schema": {
                  "$defs": {
                    "AvailabilityView": {
                      "additionalProperties": false,
                      "properties": {
                        "attendees": {
                          "title": "Attendees",
                          "type": "integer"
                        },
                        "end_at": {
                          "format": "date-time",
                          "title": "End At",
                          "type": "string"
                        },
                        "rooms": {
                          "items": {
                            "$ref": "#/$defs/RoomView"
                          },
                          "title": "Rooms",
                          "type": "array"
                        },
                        "start_at": {
                          "format": "date-time",
                          "title": "Start At",
                          "type": "string"
                        },
                        "truncated": {
                          "default": false,
                          "title": "Truncated",
                          "type": "boolean"
                        }
                      },
                      "required": [
                        "rooms",
                        "start_at",
                        "end_at",
                        "attendees"
                      ],
                      "title": "AvailabilityView",
                      "type": "object"
                    },
                    "RoomView": {
                      "additionalProperties": false,
                      "properties": {
                        "capacity": {
                          "title": "Capacity",
                          "type": "integer"
                        },
                        "room_code": {
                          "title": "Room Code",
                          "type": "string"
                        }
                      },
                      "required": [
                        "room_code",
                        "capacity"
                      ],
                      "title": "RoomView",
                      "type": "object"
                    }
                  },
                  "additionalProperties": false,
                  "properties": {
                    "code": {
                      "title": "Code",
                      "type": "string"
                    },
                    "commit_state": {
                      "enum": [
                        "not_applicable",
                        "not_committed",
                        "committed",
                        "unknown"
                      ],
                      "title": "Commit State",
                      "type": "string"
                    },
                    "data": {
                      "$ref": "#/$defs/AvailabilityView"
                    },
                    "evidence_refs": {
                      "items": {
                        "type": "string"
                      },
                      "title": "Evidence Refs",
                      "type": "array"
                    },
                    "request_key": {
                      "anyOf": [
                        {
                          "format": "uuid",
                          "type": "string"
                        },
                        {
                          "type": "null"
                        }
                      ],
                      "default": null,
                      "title": "Request Key"
                    },
                    "retryable": {
                      "default": false,
                      "title": "Retryable",
                      "type": "boolean"
                    },
                    "status": {
                      "default": "ok",
                      "enum": [
                        "ok",
                        "not_found"
                      ],
                      "title": "Status",
                      "type": "string"
                    },
                    "summary": {
                      "title": "Summary",
                      "type": "string"
                    }
                  },
                  "required": [
                    "code",
                    "summary",
                    "data",
                    "commit_state"
                  ],
                  "title": "BookingToolResult[AvailabilityView]",
                  "type": "object"
                },
                "version": "1.0"
              },
              {
                "effect": "draft_write",
                "idempotent": false,
                "input_schema": {
                  "additionalProperties": false,
                  "properties": {
                    "attendees": {
                      "maximum": 2147483647,
                      "minimum": 1,
                      "title": "Attendees",
                      "type": "integer"
                    },
                    "end_at": {
                      "format": "date-time",
                      "title": "End At",
                      "type": "string"
                    },
                    "room_code": {
                      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$",
                      "title": "Room Code",
                      "type": "string"
                    },
                    "start_at": {
                      "format": "date-time",
                      "title": "Start At",
                      "type": "string"
                    }
                  },
                  "required": [
                    "start_at",
                    "end_at",
                    "attendees",
                    "room_code"
                  ],
                  "title": "PreviewBooking",
                  "type": "object"
                },
                "name": "preview_booking",
                "output_schema": {
                  "$defs": {
                    "DraftView": {
                      "additionalProperties": false,
                      "properties": {
                        "attendees": {
                          "title": "Attendees",
                          "type": "integer"
                        },
                        "draft_hash": {
                          "pattern": "^[0-9a-f]{64}$",
                          "title": "Draft Hash",
                          "type": "string"
                        },
                        "draft_id": {
                          "format": "uuid",
                          "title": "Draft Id",
                          "type": "string"
                        },
                        "end_at": {
                          "format": "date-time",
                          "title": "End At",
                          "type": "string"
                        },
                        "expires_at": {
                          "format": "date-time",
                          "title": "Expires At",
                          "type": "string"
                        },
                        "room_code": {
                          "title": "Room Code",
                          "type": "string"
                        },
                        "start_at": {
                          "format": "date-time",
                          "title": "Start At",
                          "type": "string"
                        },
                        "status": {
                          "enum": [
                            "pending",
                            "confirmed",
                            "expired",
                            "superseded",
                            "rejected"
                          ],
                          "title": "Status",
                          "type": "string"
                        },
                        "suggested_idempotency_key": {
                          "format": "uuid",
                          "title": "Suggested Idempotency Key",
                          "type": "string"
                        }
                      },
                      "required": [
                        "draft_id",
                        "draft_hash",
                        "suggested_idempotency_key",
                        "room_code",
                        "start_at",
                        "end_at",
                        "attendees",
                        "status",
                        "expires_at"
                      ],
                      "title": "DraftView",
                      "type": "object"
                    }
                  },
                  "additionalProperties": false,
                  "properties": {
                    "code": {
                      "title": "Code",
                      "type": "string"
                    },
                    "commit_state": {
                      "enum": [
                        "not_applicable",
                        "not_committed",
                        "committed",
                        "unknown"
                      ],
                      "title": "Commit State",
                      "type": "string"
                    },
                    "data": {
                      "$ref": "#/$defs/DraftView"
                    },
                    "evidence_refs": {
                      "items": {
                        "type": "string"
                      },
                      "title": "Evidence Refs",
                      "type": "array"
                    },
                    "request_key": {
                      "anyOf": [
                        {
                          "format": "uuid",
                          "type": "string"
                        },
                        {
                          "type": "null"
                        }
                      ],
                      "default": null,
                      "title": "Request Key"
                    },
                    "retryable": {
                      "default": false,
                      "title": "Retryable",
                      "type": "boolean"
                    },
                    "status": {
                      "default": "ok",
                      "enum": [
                        "ok",
                        "not_found"
                      ],
                      "title": "Status",
                      "type": "string"
                    },
                    "summary": {
                      "title": "Summary",
                      "type": "string"
                    }
                  },
                  "required": [
                    "code",
                    "summary",
                    "data",
                    "commit_state"
                  ],
                  "title": "BookingToolResult[DraftView]",
                  "type": "object"
                },
                "version": "1.0"
              },
              {
                "effect": "booking_write",
                "idempotent": true,
                "input_schema": {
                  "additionalProperties": false,
                  "properties": {
                    "draft_hash": {
                      "pattern": "^[0-9a-f]{64}$",
                      "title": "Draft Hash",
                      "type": "string"
                    },
                    "draft_id": {
                      "format": "uuid",
                      "title": "Draft Id",
                      "type": "string"
                    },
                    "idempotency_key": {
                      "format": "uuid",
                      "title": "Idempotency Key",
                      "type": "string"
                    }
                  },
                  "required": [
                    "draft_id",
                    "draft_hash",
                    "idempotency_key"
                  ],
                  "title": "ConfirmBookingToolInput",
                  "type": "object"
                },
                "name": "confirm_booking",
                "output_schema": {
                  "$defs": {
                    "BookingView": {
                      "additionalProperties": false,
                      "properties": {
                        "attendees": {
                          "title": "Attendees",
                          "type": "integer"
                        },
                        "booking_id": {
                          "format": "uuid",
                          "title": "Booking Id",
                          "type": "string"
                        },
                        "created_at": {
                          "format": "date-time",
                          "title": "Created At",
                          "type": "string"
                        },
                        "draft_id": {
                          "format": "uuid",
                          "title": "Draft Id",
                          "type": "string"
                        },
                        "end_at": {
                          "format": "date-time",
                          "title": "End At",
                          "type": "string"
                        },
                        "idempotency_key": {
                          "format": "uuid",
                          "title": "Idempotency Key",
                          "type": "string"
                        },
                        "room_code": {
                          "title": "Room Code",
                          "type": "string"
                        },
                        "start_at": {
                          "format": "date-time",
                          "title": "Start At",
                          "type": "string"
                        },
                        "status": {
                          "enum": [
                            "confirmed",
                            "cancelled"
                          ],
                          "title": "Status",
                          "type": "string"
                        }
                      },
                      "required": [
                        "booking_id",
                        "draft_id",
                        "idempotency_key",
                        "room_code",
                        "start_at",
                        "end_at",
                        "attendees",
                        "status",
                        "created_at"
                      ],
                      "title": "BookingView",
                      "type": "object"
                    }
                  },
                  "additionalProperties": false,
                  "properties": {
                    "code": {
                      "title": "Code",
                      "type": "string"
                    },
                    "commit_state": {
                      "enum": [
                        "not_applicable",
                        "not_committed",
                        "committed",
                        "unknown"
                      ],
                      "title": "Commit State",
                      "type": "string"
                    },
                    "data": {
                      "$ref": "#/$defs/BookingView"
                    },
                    "evidence_refs": {
                      "items": {
                        "type": "string"
                      },
                      "title": "Evidence Refs",
                      "type": "array"
                    },
                    "request_key": {
                      "anyOf": [
                        {
                          "format": "uuid",
                          "type": "string"
                        },
                        {
                          "type": "null"
                        }
                      ],
                      "default": null,
                      "title": "Request Key"
                    },
                    "retryable": {
                      "default": false,
                      "title": "Retryable",
                      "type": "boolean"
                    },
                    "status": {
                      "default": "ok",
                      "enum": [
                        "ok",
                        "not_found"
                      ],
                      "title": "Status",
                      "type": "string"
                    },
                    "summary": {
                      "title": "Summary",
                      "type": "string"
                    }
                  },
                  "required": [
                    "code",
                    "summary",
                    "data",
                    "commit_state"
                  ],
                  "title": "BookingToolResult[BookingView]",
                  "type": "object"
                },
                "version": "1.0"
              },
              {
                "effect": "read",
                "idempotent": true,
                "input_schema": {
                  "additionalProperties": false,
                  "properties": {
                    "idempotency_key": {
                      "format": "uuid",
                      "type": "string"
                    }
                  },
                  "required": [
                    "idempotency_key"
                  ],
                  "type": "object"
                },
                "name": "lookup_booking_by_request",
                "output_schema": {
                  "$defs": {
                    "BookingView": {
                      "additionalProperties": false,
                      "properties": {
                        "attendees": {
                          "title": "Attendees",
                          "type": "integer"
                        },
                        "booking_id": {
                          "format": "uuid",
                          "title": "Booking Id",
                          "type": "string"
                        },
                        "created_at": {
                          "format": "date-time",
                          "title": "Created At",
                          "type": "string"
                        },
                        "draft_id": {
                          "format": "uuid",
                          "title": "Draft Id",
                          "type": "string"
                        },
                        "end_at": {
                          "format": "date-time",
                          "title": "End At",
                          "type": "string"
                        },
                        "idempotency_key": {
                          "format": "uuid",
                          "title": "Idempotency Key",
                          "type": "string"
                        },
                        "room_code": {
                          "title": "Room Code",
                          "type": "string"
                        },
                        "start_at": {
                          "format": "date-time",
                          "title": "Start At",
                          "type": "string"
                        },
                        "status": {
                          "enum": [
                            "confirmed",
                            "cancelled"
                          ],
                          "title": "Status",
                          "type": "string"
                        }
                      },
                      "required": [
                        "booking_id",
                        "draft_id",
                        "idempotency_key",
                        "room_code",
                        "start_at",
                        "end_at",
                        "attendees",
                        "status",
                        "created_at"
                      ],
                      "title": "BookingView",
                      "type": "object"
                    }
                  },
                  "additionalProperties": false,
                  "properties": {
                    "code": {
                      "title": "Code",
                      "type": "string"
                    },
                    "commit_state": {
                      "enum": [
                        "not_applicable",
                        "not_committed",
                        "committed",
                        "unknown"
                      ],
                      "title": "Commit State",
                      "type": "string"
                    },
                    "data": {
                      "anyOf": [
                        {
                          "$ref": "#/$defs/BookingView"
                        },
                        {
                          "type": "null"
                        }
                      ]
                    },
                    "evidence_refs": {
                      "items": {
                        "type": "string"
                      },
                      "title": "Evidence Refs",
                      "type": "array"
                    },
                    "request_key": {
                      "anyOf": [
                        {
                          "format": "uuid",
                          "type": "string"
                        },
                        {
                          "type": "null"
                        }
                      ],
                      "default": null,
                      "title": "Request Key"
                    },
                    "retryable": {
                      "default": false,
                      "title": "Retryable",
                      "type": "boolean"
                    },
                    "status": {
                      "default": "ok",
                      "enum": [
                        "ok",
                        "not_found"
                      ],
                      "title": "Status",
                      "type": "string"
                    },
                    "summary": {
                      "title": "Summary",
                      "type": "string"
                    }
                  },
                  "required": [
                    "code",
                    "summary",
                    "data",
                    "commit_state"
                  ],
                  "title": "BookingToolResult[Union[BookingView, NoneType]]",
                  "type": "object"
                },
                "version": "1.0"
              }
            ],
            "trusted_context": [
              "actor_key",
              "scope_id",
              "reference_clock",
              "confirmed"
            ],
            "unknown_commit": "lookup original idempotency key; never replace key",
            "version": "1.0"
          },
          "version": "booking-registry-v1"
        },
        "underlying_version": "not independently observable; official mapping recorded in pricing",
        "validation_boundary": "protocol evidence here; dialogue outcomes recorded separately"
      },
      "target_version_id": 1,
      "trace_schema": "1.3"
    },
    "manifest_hash": "e5751a48b6ac9862ff005eaf3285f4d0248486076ccb56c66d12fc58680247ff",
    "run_id": "5f7ffc80-cdf0-4a19-9bda-ebe3118256fe",
    "started_at": "2026-10-29 07:35:37.387721+00:00",
    "status": "completed",
    "summary": {
      "counts": {
        "failed": 1
      },
      "evaluation": "awaiting_execution",
      "ledger": {}
    }
  },
  "schema": "agentchaos-report-v1",
  "source_event_count": 27,
  "statistics": {
    "ai_assistance_outcomes": {
      "not_evaluated": 1
    },
    "attempts_including_recovery": 1,
    "execution_status_counts": {
      "failed": 1
    },
    "planned_executions": 1,
    "reliability": {
      "denominator": 1,
      "exclusions": {},
      "numerator": 0,
      "rate": 0.0,
      "unit": "execution, latest rule version; repeats are not independent families"
    },
    "rule_outcomes": {
      "failed": 1
    },
    "task_fulfilment": {
      "denominator": 0,
      "exclusions": {
        "unknown": 1
      },
      "numerator": 0,
      "rate": null
    },
    "task_outcomes": {
      "unknown": 1
    },
    "unique_cases": 1,
    "usage_boundary": "Reported usage is retained verbatim. Missing/unknown is not zero or a provider bill.",
    "usage_records": [
      {
        "execution_id": "aa412098-8ddc-4d2b-b32a-40e5610e027e",
        "reported": {
          "roles": {},
          "tickets": {}
        }
      }
    ]
  },
  "target": {
    "adapter_revision": "langgraph-booking-v1",
    "config_hash": "2fe0332d3afe4efb25cd725271b70dfcdf33871688ecc297ea91498ee1a94468",
    "graph_revision": "T04-V0-2fe0332d3afe",
    "name": "场地预约实验对象",
    "prompt_hash": "0db5754ab85de312ce138eeb814294e25f8bdc6b2797ba85b80b70aec147b025",
    "target_id": 1,
    "tool_revision": "booking-registry-v1",
    "version_id": 1,
    "version_no": 1
  }
}
```
