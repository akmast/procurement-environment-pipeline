{
  "Comment": "Rebuilds the Gold Layer (see gold/<source>/*.py) — one combined Parquet file per source (Eurostat, EEA, TED), each always built from EVERY country/year/pollutant currently available in that source's precursor stage (normalization for Eurostat, which has no transformation stage; transformation for EEA/TED). Manual only, never on a schedule and never auto-chained after Historical/Update — Gold is a deliberate separate rebuild step you run once the sources you care about have been ingested/normalized/transformed (see docs/pipelines/gold_layer.md). External input: {} — no fields are required or read; each branch always passes --discover, so there is nothing for a caller to select. The three sources are independent (no shared precursor, no ordering between them), so they run in parallel and one source failing doesn't stop the others.",
  "StartAt": "GenerateRunId",
  "States": {
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
        "run_id.$": "$.generated.run_id"
      },
      "Next": "RunGoldBuilds"
    },
    "RunGoldBuilds": {
      "Type": "Parallel",
      "Next": "EvaluateOverallStatus",
      "ResultPath": "$.branch_results",
      "Branches": [
        {
          "StartAt": "RunEurostatGold",
          "States": {
            "RunEurostatGold": {
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
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EurostatGoldFailed" }
              ],
              "Next": "EurostatGoldSucceeded"
            },
            "EurostatGoldSucceeded": { "Type": "Pass", "Parameters": { "source": "eurostat", "status": "SUCCEEDED" }, "End": true },
            "EurostatGoldFailed": { "Type": "Pass", "Parameters": { "source": "eurostat", "status": "FAILED" }, "End": true }
          }
        },
        {
          "StartAt": "RunEeaGold",
          "States": {
            "RunEeaGold": {
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
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EeaGoldFailed" }
              ],
              "Next": "EeaGoldSucceeded"
            },
            "EeaGoldSucceeded": { "Type": "Pass", "Parameters": { "source": "eea", "status": "SUCCEEDED" }, "End": true },
            "EeaGoldFailed": { "Type": "Pass", "Parameters": { "source": "eea", "status": "FAILED" }, "End": true }
          }
        },
        {
          "StartAt": "RunTedGold",
          "States": {
            "RunTedGold": {
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
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "TedGoldFailed" }
              ],
              "Next": "TedGoldSucceeded"
            },
            "TedGoldSucceeded": { "Type": "Pass", "Parameters": { "source": "ted", "status": "SUCCEEDED" }, "End": true },
            "TedGoldFailed": { "Type": "Pass", "Parameters": { "source": "ted", "status": "FAILED" }, "End": true }
          }
        }
      ]
    },
    "EvaluateOverallStatus": {
      "Type": "Choice",
      "Choices": [
        { "Variable": "$.branch_results[0].status", "StringEquals": "FAILED", "Next": "GoldStandardFailed" },
        { "Variable": "$.branch_results[1].status", "StringEquals": "FAILED", "Next": "GoldStandardFailed" },
        { "Variable": "$.branch_results[2].status", "StringEquals": "FAILED", "Next": "GoldStandardFailed" }
      ],
      "Default": "GoldStandardSucceeded"
    },
    "GoldStandardSucceeded": { "Type": "Succeed" },
    "GoldStandardFailed": {
      "Type": "Fail",
      "Error": "GoldStandardPipelineFailed",
      "Cause": "At least one source's Gold build failed — see branch_results and CloudWatch Logs for the failing source."
    }
  }
}
