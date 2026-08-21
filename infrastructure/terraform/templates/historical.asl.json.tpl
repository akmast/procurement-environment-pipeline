{
  "Comment": "Manual-only historical backfill for the requested source families in parallel. Never triggered by a schedule — only via workflow_dispatch (run-pipeline.yml) or a manual StartExecution. External input: {\"sources\": [\"eea\", \"ted\", \"eurostat\"], \"countries_csv\": \"DE,PL\", \"from_year\": 2021, \"to_year\": 2025} — TED's date-based API gets from_year/to_year translated to from_date/to_date internally (Python-equivalent logic, done here in ASL via States.Format, not duplicated business logic — just year-to-date-string formatting).",
  "StartAt": "CheckBootstrapComplete",
  "States": {
    "CheckBootstrapComplete": {
      "Type": "Task",
      "Resource": "arn:aws:states:::ecs:runTask.sync",
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
      "Next": "GenerateRunId"
    },
    "BootstrapIncomplete": {
      "Type": "Fail",
      "Error": "BootstrapIncomplete",
      "Cause": "Bootstrap reference data is missing or incomplete — run BootstrapReferenceStateMachine first (see docs/aws/operations.md)."
    },
    "GenerateRunId": {
      "Type": "Pass",
      "Parameters": {
        "run_id.$": "States.UUID()"
      },
      "ResultPath": "$.generated",
      "Next": "SetRunId"
    },
    "SetRunId": {
      "Type": "Pass",
      "Parameters": {
        "run_id.$": "$.generated.run_id",
        "sources.$": "$.sources",
        "countries_csv.$": "$.countries_csv",
        "from_year.$": "$.from_year",
        "to_year.$": "$.to_year"
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
                { "Variable": "$.check.requested", "BooleanEquals": true, "Next": "EeaRunIngestion" }
              ],
              "Default": "EeaSkipped"
            },
            "EeaRunIngestion": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
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
                { "Variable": "$.check.requested", "BooleanEquals": true, "Next": "TedRunIngestion" }
              ],
              "Default": "TedSkipped"
            },
            "TedRunIngestion": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
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
                { "Variable": "$.check.requested", "BooleanEquals": true, "Next": "EurostatRunIngestion" }
              ],
              "Default": "EurostatSkipped"
            },
            "EurostatRunIngestion": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
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
