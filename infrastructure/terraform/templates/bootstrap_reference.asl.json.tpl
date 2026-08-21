{
  "Comment": "Prepares reference/lookup data (NUTS boundaries, TED codelists, EEA stations) — manual only, never run automatically and never on a schedule. Historical/update refuse to run their main-data stages until this has completed successfully (see common/bootstrap.py). External input: {\"countries_csv\": \"DE,PL\"} — the EEA stations chain's country scope; NUTS boundaries and TED codelists are EU-wide and take no countries.",
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
        "run_id.$": "$.generated.run_id",
        "countries_csv.$": "$.countries_csv"
      },
      "Next": "RunReferencePipelines"
    },
    "RunReferencePipelines": {
      "Type": "Parallel",
      "Next": "WriteBootstrapManifest",
      "ResultPath": "$.branch_results",
      "Branches": [
        {
          "StartAt": "RunNutsBoundariesIngestion",
          "States": {
            "RunNutsBoundariesIngestion": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
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
                      "Command.$": "States.Array('stage', '--source', 'eea-nuts-boundaries', '--stage', 'ingestion', '--storage-mode', 'cloud', '--run-id', $.run_id)"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "NutsBoundariesFailed" }
              ],
              "Next": "NutsBoundariesSucceeded"
            },
            "NutsBoundariesSucceeded": { "Type": "Pass", "Parameters": { "source": "eea-nuts-boundaries", "status": "SUCCEEDED" }, "End": true },
            "NutsBoundariesFailed": { "Type": "Pass", "Parameters": { "source": "eea-nuts-boundaries", "status": "FAILED" }, "End": true }
          }
        },
        {
          "StartAt": "RunTedCodelistsIngestion",
          "States": {
            "RunTedCodelistsIngestion": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
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
                      "Command.$": "States.Array('stage', '--source', 'ted-codelists', '--stage', 'ingestion', '--storage-mode', 'cloud', '--run-id', $.run_id)"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "TedCodelistsFailed" }
              ],
              "Next": "RunTedCodelistsNormalization"
            },
            "RunTedCodelistsNormalization": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
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
                      "Command.$": "States.Array('stage', '--source', 'ted-codelists', '--stage', 'normalization', '--storage-mode', 'cloud', '--run-id', $.run_id, '--input-manifest', States.Format('s3://${data_bucket_name}/runs/{}/ted-codelists/ingestion.json', $.run_id))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "TedCodelistsFailed" }
              ],
              "Next": "TedCodelistsSucceeded"
            },
            "TedCodelistsSucceeded": { "Type": "Pass", "Parameters": { "source": "ted-codelists", "status": "SUCCEEDED" }, "End": true },
            "TedCodelistsFailed": { "Type": "Pass", "Parameters": { "source": "ted-codelists", "status": "FAILED" }, "End": true }
          }
        },
        {
          "StartAt": "RunEeaStationsIngestion",
          "States": {
            "RunEeaStationsIngestion": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
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
                      "Command.$": "States.Array('stage', '--source', 'eea-stations', '--stage', 'ingestion', '--mode', 'stations', '--storage-mode', 'cloud', '--run-id', $.run_id, '--countries-csv', $.countries_csv)"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EeaStationsFailed" }
              ],
              "Next": "RunEeaStationsNormalization"
            },
            "RunEeaStationsNormalization": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
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
                      "Command.$": "States.Array('stage', '--source', 'eea-stations', '--stage', 'normalization', '--storage-mode', 'cloud', '--run-id', $.run_id, '--input-manifest', States.Format('s3://${data_bucket_name}/runs/{}/eea-stations/ingestion.json', $.run_id))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EeaStationsFailed" }
              ],
              "Next": "RunEeaStationsTransformation"
            },
            "RunEeaStationsTransformation": {
              "Type": "Task",
              "Resource": "arn:aws:states:::ecs:runTask.sync",
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
                      "Command.$": "States.Array('stage', '--source', 'eea-stations', '--stage', 'transformation', '--storage-mode', 'cloud', '--run-id', $.run_id, '--input-manifest', States.Format('s3://${data_bucket_name}/runs/{}/eea-stations/normalization.json', $.run_id))"
                    }
                  ]
                }
              },
              "Retry": [
                { "ErrorEquals": ["ECS.AmazonECSException", "States.Timeout"], "IntervalSeconds": 30, "MaxAttempts": 3, "BackoffRate": 2.0 }
              ],
              "Catch": [
                { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "EeaStationsFailed" }
              ],
              "Next": "EeaStationsSucceeded"
            },
            "EeaStationsSucceeded": { "Type": "Pass", "Parameters": { "source": "eea-stations", "status": "SUCCEEDED" }, "End": true },
            "EeaStationsFailed": { "Type": "Pass", "Parameters": { "source": "eea-stations", "status": "FAILED" }, "End": true }
          }
        }
      ]
    },
    "WriteBootstrapManifest": {
      "Type": "Task",
      "Resource": "arn:aws:states:::ecs:runTask.sync",
      "TimeoutSeconds": 300,
      "Comment": "Re-checks required reference outputs against real S3 state and writes system/bootstrap/reference/latest.json — the authoritative signal, independent of branch_results above (a step can report SUCCEEDED yet still leave a required file missing due to a partial/edge-case failure; this task is what actually decides COMPLETE vs INCOMPLETE).",
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
              "Command": ["write-bootstrap-manifest", "--storage-mode", "cloud"]
            }
          ]
        }
      },
      "Catch": [
        { "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "BootstrapFailed" }
      ],
      "Next": "BootstrapSucceeded"
    },
    "BootstrapSucceeded": { "Type": "Succeed" },
    "BootstrapFailed": {
      "Type": "Fail",
      "Error": "BootstrapReferenceFailed",
      "Cause": "Bootstrap reference data preparation did not complete — see branch_results, the bootstrap manifest at system/bootstrap/reference/latest.json, and CloudWatch Logs for what's missing."
    }
  }
}
