{
  "Comment": "Manual-only historical backfill for the requested source families in parallel. Never triggered by a schedule — only via workflow_dispatch (run-pipeline.yml) or a manual StartExecution. External input: {\"sources\": [\"eea\", \"ted\", \"eurostat\"], \"countries_csv\": \"DE,PL\", \"from_year\": 2021, \"to_year\": 2025} — TED's date-based API gets from_year/to_year translated to from_date/to_date internally (Python-equivalent logic, done here in ASL via States.Format, not duplicated business logic — just year-to-date-string formatting). Optional resume fields: \"run_id\" (reuse a previous execution's run_id instead of generating a new one — this is what makes resume work at all, since every stage's manifest already lives at a run_id-keyed S3 path) and \"start_stage\" (\"ingestion\" default / \"normalization\" / \"transformation\" — skips the earlier RunTask states for every requested source and jumps straight to that stage, reading the prior stage's own manifest at runs/<run_id>/<source>/<prior_stage>.json as --input-manifest, same as the normal chaining does). Only pass start_stage together with a matching run_id — resuming without reusing the original run_id has no prior manifest to read. Gold Layer (see docs/pipelines/gold_layer.md) is rebuilt automatically at the end of each source's own branch — from transformation for eea/ted, straight from normalization for eurostat (which has no transformation stage) — but only if that branch's last data stage actually wrote something new this run (main.py check-manifest-has-output); if nothing changed, Gold is skipped and the branch still reports SUCCEEDED. There is no separate Gold-only state machine — to force a rebuild without new source data, rerun this state machine for the sources you care about.",
  "StartAt": "CheckBootstrapComplete",
  "States": {
    "CheckBootstrapComplete": {
      "Type": "Task",
      "Resource": "arn:aws:states:::ecs:runTask.sync",
      "ResultPath": null,
      "TimeoutSeconds": 300,
      "Comment": "Refuses to run any main-data stage if reference data (NUTS boundaries, EEA stations, TED codelists) hasn't been prepared by BootstrapReferenceStateMachine — see common/bootstrap.py.",
      "Parameters": {
        "LaunchType": "FARGATE",
        "Cluster": "${ecs_cluster_arn}",
        "TaskDefinition": "${ecs_task_definition_arn}",
        "PropagateTags": "TASK_DEFINITION",
        "EnableECSManagedTags": true,
        "NetworkConfiguration": {
          "AwsvpcConfiguration": {
            "Subnets": ${subnet_ids_json},
            "SecurityGroups": ${security_group_ids_json},
            "AssignPublicIp": "ENABLED"
          }
        },
        "Overrides": {
          "ContainerOverrides": [
            {
              "Name": "${container_name}",
              "Command": ["check-bootstrap-complete", "--storage-mode", "cloud"]
            }
          ]
        }
      },
      "Catch": [
        { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "BootstrapIncomplete" }
      ],
      "Next": "RunIdProvided"
    },
    "BootstrapIncomplete": {
      "Type": "Fail",
      "Error": "BootstrapIncomplete",
      "Cause": "Bootstrap reference data is missing or incomplete — run BootstrapReferenceStateMachine first (see docs/aws/operations.md)."
    },
    "RunIdProvided": {
      "Type": "Choice",
      "Comment": "A resume: caller supplied run_id (see the state machine's top-level Comment) — reuse it and honor start_stage instead of generating a fresh run_id and starting every requested source at ingestion.",
      "Choices": [
        { "Variable": "$.run_id", "IsPresent": true, "Next": "SetRunIdFromInput" }
      ],
      "Default": "GenerateRunId"
    },
    "GenerateRunId": {
      "Type": "Pass",
      "Parameters": {
        "run_id.$": "States.UUID()"
      },
      "ResultPath": "$.generated",
      "Next": "SetRunIdFromGenerated"
    },
    "SetRunIdFromGenerated": {
      "Type": "Pass",
      "Parameters": {
        "run_id.$": "$.generated.run_id",
        "sources.$": "$.sources",
        "countries_csv.$": "$.countries_csv",
        "from_year.$": "$.from_year",
        "to_year.$": "$.to_year",
        "start_stage": "ingestion"
      },
      "Next": "RunSources"
    },
    "SetRunIdFromInput": {
      "Type": "Pass",
      "Parameters": {
        "run_id.$": "$.run_id",
        "sources.$": "$.sources",
        "countries_csv.$": "$.countries_csv",
        "from_year.$": "$.from_year",
        "to_year.$": "$.to_year",
        "start_stage.$": "$.start_stage"
      },
      "Next": "RunSources"
    },
    "RunSources": {
      "Type": "Parallel",
      "Next": "EvaluateOverallStatus",
      "ResultPath": "$.branch_results",
      "Branches": [
        {
          "StartAt": "EeaCheckRequested",
          "States": {
            "EeaCheckRequested": {
              "Type": "Pass",
              "Parameters": { "requested.$": "States.ArrayContains($.sources, 'eea')" },
              "ResultPath": "$.check",
              "Next": "EeaRequested"
            },
            "EeaRequested": {
              "Type": "Choice",
              "Choices": [
                { "Variable": "$.check.requested", "BooleanEquals": true, "Next": "EeaCheckStartStage" }
              ],
              "Default": "EeaSkipped"
            },
            "EeaCheckStartStage": {
              "Type": "Choice",
              "Comment": "Resume support: start_stage (default \"ingestion\") skips straight to the requested stage's RunTask, which reads the prior stage's own manifest from S3 (runs/<run_id>/eea-measurements/<prior_stage>.json) via --input-manifest — present already if run_id is a reused/resumed run.",
              "Choices": [
                { "Variable": "$.start_stage", "StringEquals": "normalization", "Next": "EeaRunNormalization" },
                { "Variable": "$.start_stage", "StringEquals": "transformation", "Next": "EeaRunTransformation" }
              ],
              "Default": "EeaRunIngestion"
            },
            "EeaRunIngestion": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 21600,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'eea-measurements', '--stage', 'ingestion', '--mode', 'historical', '--storage-mode', 'cloud', '--run-id', $.run_id, '--countries-csv', $.countries_csv, '--from-year', States.Format('{}', $.from_year), '--to-year', States.Format('{}', $.to_year))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EeaFailed" }
              ],
              "Next": "EeaRunNormalization"
            },
            "EeaRunNormalization": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 3600,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'eea-measurements', '--stage', 'normalization', '--storage-mode', 'cloud', '--run-id', $.run_id, '--input-manifest', States.Format('s3://${data_bucket_name}/runs/{}/eea-measurements/ingestion.json', $.run_id))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EeaFailed" }
              ],
              "Next": "EeaRunTransformation"
            },
            "EeaRunTransformation": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 3600,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'eea-measurements', '--stage', 'transformation', '--storage-mode', 'cloud', '--run-id', $.run_id, '--input-manifest', States.Format('s3://${data_bucket_name}/runs/{}/eea-measurements/normalization.json', $.run_id))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EeaFailed" }
              ],
              "Next": "EeaCheckHasNewData"
            },
            "EeaCheckHasNewData": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 120,
              "Comment": "Gates the Gold rebuild below on whether this run actually changed eea-measurements' transformed data (main.py check-manifest-has-output) — a non-zero exit (nothing new) is Caught straight to EeaSucceeded, skipping Gold, not treated as a real failure.",
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('check-manifest-has-output', '--run-id', $.run_id, '--source', 'eea-measurements', '--stage', 'transformation', '--storage-mode', 'cloud')"
                    }
                  ]
                }
              },
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EeaSucceeded" }
              ],
              "Next": "EeaRunGold"
            },
            "EeaRunGold": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 3600,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'eea-measurements', '--stage', 'gold', '--discover', '--storage-mode', 'cloud', '--run-id', $.run_id)"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EeaFailed" }
              ],
              "Next": "EeaSucceeded"
            },
            "EeaSucceeded": { "Type": "Pass", "Parameters": { "source": "eea", "status": "SUCCEEDED" }, "End": true },
            "EeaSkipped": { "Type": "Pass", "Parameters": { "source": "eea", "status": "SKIPPED" }, "End": true },
            "EeaFailed": { "Type": "Pass", "Parameters": { "source": "eea", "status": "FAILED" }, "End": true }
          }
        },
        {
          "StartAt": "TedCheckRequested",
          "States": {
            "TedCheckRequested": {
              "Type": "Pass",
              "Parameters": { "requested.$": "States.ArrayContains($.sources, 'ted')" },
              "ResultPath": "$.check",
              "Next": "TedRequested"
            },
            "TedRequested": {
              "Type": "Choice",
              "Choices": [
                { "Variable": "$.check.requested", "BooleanEquals": true, "Next": "TedCheckStartStage" }
              ],
              "Default": "TedSkipped"
            },
            "TedCheckStartStage": {
              "Type": "Choice",
              "Comment": "Resume support: start_stage (default \"ingestion\") skips straight to the requested stage's RunTask, which reads the prior stage's own manifest from S3 (runs/<run_id>/ted-notices/<prior_stage>.json) via --input-manifest — present already if run_id is a reused/resumed run.",
              "Choices": [
                { "Variable": "$.start_stage", "StringEquals": "normalization", "Next": "TedRunNormalization" },
                { "Variable": "$.start_stage", "StringEquals": "transformation", "Next": "TedRunTransformation" }
              ],
              "Default": "TedRunIngestion"
            },
            "TedRunIngestion": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 21600,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'ted-notices', '--stage', 'ingestion', '--mode', 'historical', '--storage-mode', 'cloud', '--run-id', $.run_id, '--countries-csv', $.countries_csv, '--from-date', States.Format('{}-01-01', $.from_year), '--to-date', States.Format('{}-12-31', $.to_year))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "TedFailed" }
              ],
              "Next": "TedRunNormalization"
            },
            "TedRunNormalization": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 3600,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'ted-notices', '--stage', 'normalization', '--storage-mode', 'cloud', '--run-id', $.run_id, '--input-manifest', States.Format('s3://${data_bucket_name}/runs/{}/ted-notices/ingestion.json', $.run_id))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "TedFailed" }
              ],
              "Next": "TedRunTransformation"
            },
            "TedRunTransformation": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 3600,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'ted-notices', '--stage', 'transformation', '--storage-mode', 'cloud', '--run-id', $.run_id, '--input-manifest', States.Format('s3://${data_bucket_name}/runs/{}/ted-notices/normalization.json', $.run_id))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "TedFailed" }
              ],
              "Next": "TedCheckHasNewData"
            },
            "TedCheckHasNewData": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 120,
              "Comment": "Gates the Gold rebuild below on whether this run actually changed ted-notices' transformed data (main.py check-manifest-has-output) — a non-zero exit (nothing new) is Caught straight to TedSucceeded, skipping Gold, not treated as a real failure.",
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('check-manifest-has-output', '--run-id', $.run_id, '--source', 'ted-notices', '--stage', 'transformation', '--storage-mode', 'cloud')"
                    }
                  ]
                }
              },
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "TedSucceeded" }
              ],
              "Next": "TedRunGold"
            },
            "TedRunGold": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 900,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'ted-notices', '--stage', 'gold', '--discover', '--storage-mode', 'cloud', '--run-id', $.run_id)"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "TedFailed" }
              ],
              "Next": "TedSucceeded"
            },
            "TedSucceeded": { "Type": "Pass", "Parameters": { "source": "ted", "status": "SUCCEEDED" }, "End": true },
            "TedSkipped": { "Type": "Pass", "Parameters": { "source": "ted", "status": "SKIPPED" }, "End": true },
            "TedFailed": { "Type": "Pass", "Parameters": { "source": "ted", "status": "FAILED" }, "End": true }
          }
        },
        {
          "StartAt": "EurostatCheckRequested",
          "States": {
            "EurostatCheckRequested": {
              "Type": "Pass",
              "Parameters": { "requested.$": "States.ArrayContains($.sources, 'eurostat')" },
              "ResultPath": "$.check",
              "Next": "EurostatRequested"
            },
            "EurostatRequested": {
              "Type": "Choice",
              "Choices": [
                { "Variable": "$.check.requested", "BooleanEquals": true, "Next": "EurostatCheckStartStage" }
              ],
              "Default": "EurostatSkipped"
            },
            "EurostatCheckStartStage": {
              "Type": "Choice",
              "Comment": "Resume support: start_stage \"normalization\" skips straight to EurostatRunNormalization, reading runs/<run_id>/eurostat-agriculture-accounts/ingestion.json via --input-manifest. There is no transformation stage for this source (see main.py FAMILY_STAGES) — start_stage \"transformation\" has nothing to jump to, so it falls back to the full ingestion->normalization run like the default \"ingestion\".",
              "Choices": [
                { "Variable": "$.start_stage", "StringEquals": "normalization", "Next": "EurostatRunNormalization" }
              ],
              "Default": "EurostatRunIngestion"
            },
            "EurostatRunIngestion": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 10800,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'eurostat-agriculture-accounts', '--stage', 'ingestion', '--mode', 'historical', '--storage-mode', 'cloud', '--run-id', $.run_id, '--countries-csv', $.countries_csv, '--from-year', States.Format('{}', $.from_year), '--to-year', States.Format('{}', $.to_year))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EurostatFailed" }
              ],
              "Next": "EurostatRunNormalization"
            },
            "EurostatRunNormalization": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 3600,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'eurostat-agriculture-accounts', '--stage', 'normalization', '--storage-mode', 'cloud', '--run-id', $.run_id, '--input-manifest', States.Format('s3://${data_bucket_name}/runs/{}/eurostat-agriculture-accounts/ingestion.json', $.run_id))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EurostatFailed" }
              ],
              "Next": "EurostatCheckHasNewData"
            },
            "EurostatCheckHasNewData": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 120,
              "Comment": "Gates the Gold rebuild below on whether this run actually changed eurostat-agriculture-accounts' data (main.py check-manifest-has-output) — checked against the NORMALIZATION manifest, since this source has no transformation stage. A non-zero exit (nothing new) is Caught straight to EurostatSucceeded, skipping Gold, not treated as a real failure.",
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('check-manifest-has-output', '--run-id', $.run_id, '--source', 'eurostat-agriculture-accounts', '--stage', 'normalization', '--storage-mode', 'cloud')"
                    }
                  ]
                }
              },
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EurostatSucceeded" }
              ],
              "Next": "EurostatRunGold"
            },
            "EurostatRunGold": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
              "ResultPath": null,
              "TimeoutSeconds": 1800,
              "Parameters": {
                "LaunchType": "FARGATE",
                "Cluster": "${ecs_cluster_arn}",
                "TaskDefinition": "${ecs_task_definition_arn}",
                "PropagateTags": "TASK_DEFINITION",
                "EnableECSManagedTags": true,
                "NetworkConfiguration": {
                  "AwsvpcConfiguration": {
                    "Subnets": ${subnet_ids_json},
                    "SecurityGroups": ${security_group_ids_json},
                    "AssignPublicIp": "ENABLED"
                  }
                },
                "Overrides": {
                  "ContainerOverrides": [
                    {
                      "Name": "${container_name}",
                      "Command.$": "States.Array('stage', '--source', 'eurostat-agriculture-accounts', '--stage', 'gold', '--discover', '--storage-mode', 'cloud', '--run-id', $.run_id)"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EurostatFailed" }
              ],
              "Next": "EurostatSucceeded"
            },
            "EurostatSucceeded": { "Type": "Pass", "Parameters": { "source": "eurostat", "status": "SUCCEEDED" }, "End": true },
            "EurostatSkipped": { "Type": "Pass", "Parameters": { "source": "eurostat", "status": "SKIPPED" }, "End": true },
            "EurostatFailed": { "Type": "Pass", "Parameters": { "source": "eurostat", "status": "FAILED" }, "End": true }
          }
        }
      ]
    },
    "EvaluateOverallStatus": {
      "Type": "Choice",
      "Choices": [
        { "Variable": "$.branch_results[0].status", "StringEquals": "FAILED", "Next": "HistoricalFailed" },
        { "Variable": "$.branch_results[1].status", "StringEquals": "FAILED", "Next": "HistoricalFailed" },
        { "Variable": "$.branch_results[2].status", "StringEquals": "FAILED", "Next": "HistoricalFailed" }
      ],
      "Default": "HistoricalSucceeded"
    },
    "HistoricalSucceeded": { "Type": "Succeed" },
    "HistoricalFailed": {
      "Type": "Fail",
      "Error": "HistoricalPipelineFailed",
      "Cause": "At least one source family failed during the historical run — see branch_results and CloudWatch Logs for the failing stage."
    }
  }
}
