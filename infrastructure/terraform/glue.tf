# Glue Data Catalog for the Gold Layer — one database, three tables,
# each pointing straight at a Gold parquet file's own directory
# (`data/gold/<source>/`, see docs/pipelines/gold_layer.md). Tables are
# defined explicitly here rather than discovered by a Glue Crawler:
# each Gold build already produces one fixed, fully-documented schema
# (see gold/<source>/*.py's RENAME dicts), so a crawler would only add
# recurring cost and IAM surface for schema inference this project
# doesn't need. If a Gold column set ever changes, update the matching
# table below in the same change.
#
# Column types were taken from the actual pandas/pyarrow dtypes each
# Gold build produces (normalization's astype/to_datetime calls,
# transformation's TED date parsing — see gold/*/*.py and
# normalization/*/*.py), not guessed from column names. Notably:
# EEA's pollutant_code/validity_code/verification_code are numeric
# (Int64) EEA vocabulary codes, not strings.
resource "aws_glue_catalog_database" "gold" {
  name        = var.athena_database_name
  description = "Gold Layer tables (EEA, TED, Eurostat) for Athena/Metabase analytics."
}

resource "aws_glue_catalog_table" "eea_measurements" {
  name          = "eea_measurements"
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL              = "TRUE"
    "parquet.compression" = "SNAPPY"
  }

  storage_descriptor {
    location      = "s3://${var.data_bucket_name}/data/gold/eea/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    columns {
      name = "country_code"
      type = "string"
    }
    columns {
      name = "sampling_point_id"
      type = "string"
    }
    columns {
      name = "pollutant_code"
      type = "bigint"
    }
    columns {
      name = "measurement_period_start"
      type = "timestamp"
    }
    columns {
      name = "measurement_period_end"
      type = "timestamp"
    }
    columns {
      name = "measurement_value"
      type = "double"
    }
    columns {
      name = "measurement_unit"
      type = "string"
    }
    columns {
      name = "validity_code"
      type = "bigint"
    }
    columns {
      name = "verification_code"
      type = "bigint"
    }
    columns {
      name = "result_timestamp"
      type = "timestamp"
    }
    columns {
      name = "station_location"
      type = "string"
    }
    columns {
      name = "nuts1"
      type = "string"
    }
    columns {
      name = "nuts2"
      type = "string"
    }
    columns {
      name = "nuts3"
      type = "string"
    }
  }
}

resource "aws_glue_catalog_table" "ted_notices" {
  name          = "ted_notices"
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL              = "TRUE"
    "parquet.compression" = "SNAPPY"
  }

  storage_descriptor {
    location      = "s3://${var.data_bucket_name}/data/gold/ted/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    columns {
      name = "country_code"
      type = "string"
    }
    columns {
      name = "notice_publication_number"
      type = "string"
    }
    columns {
      name = "notice_publication_date"
      type = "date"
    }
    columns {
      name = "contract_conclusion_date"
      type = "date"
    }
    columns {
      name = "buyer_name"
      type = "string"
    }
    columns {
      name = "contract_total_value"
      type = "double"
    }
    columns {
      name = "contract_currency_code"
      type = "string"
    }
    columns {
      name = "place_of_performance_nuts"
      type = "string"
    }
    columns {
      name = "nuts1"
      type = "string"
    }
    columns {
      name = "nuts2"
      type = "string"
    }
    columns {
      name = "nuts3"
      type = "string"
    }
    columns {
      name = "place_of_performance_nuts_label"
      type = "string"
    }
    columns {
      name = "nuts1_label"
      type = "string"
    }
  }
}

resource "aws_glue_catalog_table" "eurostat_agriculture_accounts" {
  name          = "eurostat_agriculture_accounts"
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL              = "TRUE"
    "parquet.compression" = "SNAPPY"
  }

  storage_descriptor {
    location      = "s3://${var.data_bucket_name}/data/gold/eurostat/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    columns {
      name = "country_code"
      type = "string"
    }
    columns {
      name = "frequency_code"
      type = "string"
    }
    columns {
      name = "frequency_label"
      type = "string"
    }
    columns {
      name = "agricultural_item_code"
      type = "string"
    }
    columns {
      name = "agricultural_item_label"
      type = "string"
    }
    columns {
      name = "agricultural_indicator_code"
      type = "string"
    }
    columns {
      name = "agricultural_indicator_label"
      type = "string"
    }
    columns {
      name = "unit_label"
      type = "string"
    }
    columns {
      name = "nuts2"
      type = "string"
    }
    columns {
      name = "nuts2_label"
      type = "string"
    }
    columns {
      name = "reference_year"
      type = "bigint"
    }
    columns {
      name = "indicator_value"
      type = "double"
    }
  }
}
