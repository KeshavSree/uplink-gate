"""Optional sink that writes every telemetry channel reading to InfluxDB."""

from __future__ import annotations

import logging
from typing import Any

from gate.telemetry import ChannelReading

log = logging.getLogger(__name__)


class InfluxSink:
    """Writes readings as points in the `telemetry` measurement.

    Tags: vehicle, channel. Numeric values go to the field `value`, everything else to the
    field `text`, because one InfluxDB field cannot hold both numbers and strings.
    """

    def __init__(self, url: str, token: str, org: str, bucket: str) -> None:
        from influxdb_client import InfluxDBClient, WriteOptions

        self._bucket = bucket
        self._org = org
        self._client = InfluxDBClient(url=url, token=token, org=org)
        self._write_api = self._client.write_api(
            write_options=WriteOptions(batch_size=500, flush_interval=1000, max_retries=2)
        )

    def write(self, reading: ChannelReading) -> None:
        from influxdb_client import Point

        point = Point("telemetry").tag("vehicle", reading.vehicle_id).tag("channel", reading.name)
        value: Any = reading.value
        if isinstance(value, bool | int | float):
            point = point.field("value", float(value))
        else:
            point = point.field("text", str(value))
        point = point.time(reading.received_at)
        try:
            self._write_api.write(bucket=self._bucket, org=self._org, record=point)
        except Exception:  # telemetry storage must never break the gate
            log.debug("influx write failed", exc_info=True)

    def close(self) -> None:
        self._write_api.close()
        self._client.close()
