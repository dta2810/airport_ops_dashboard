from pyspark import pipelines as dp

LANDING = spark.conf.get("airport_ops.landing_path")


@dp.table(name="bronze_flight_milestones", cluster_by=["event_date", "airport_iata"],
          comment="Raw flight milestone events (append-only) from the landing volume")
def bronze_flight_milestones():
    return (spark.readStream.format("cloudFiles")
            .option("cloudFiles.format", "json")
            .option("cloudFiles.inferColumnTypes", "true")
            # contract §1 types; JSON inference would leave timestamps as strings
            .option("cloudFiles.schemaHints", "event_ts TIMESTAMP, emitted_ts TIMESTAMP, event_date DATE, "
                    "std_utc TIMESTAMP, sta_utc TIMESTAMP, delay_minutes INT")
            .load(LANDING)
            .selectExpr("*", "_metadata.file_path AS source_file", "current_timestamp() AS ingested_at"))
