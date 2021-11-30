#!/usr/bin/env python3


from asyncio import get_event_loop
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus # import UNAUTHORIZED, UNSUPPORTED_MEDIA_TYPE, BAD_REQUEST
from time import time
from urllib.parse import urlparse

import os
import re

from starlette.applications import Starlette
from starlette.responses import Response, JSONResponse, HTMLResponse, RedirectResponse
from starlette.routing import Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from aioinflux import InfluxDBClient


AIVEN_INFLUX_DSN = os.getenv("AIVEN_INFLUX_DSN")


class ErrorResponse(Response):
    """Starlette helper class to more easily return error responses."""
    media_type = "text/html"
    def __init__(self, status):
        super().__init__(content=status.phrase, status_code=status.value)


def measurement_json(sensor_name, measurement_time, measurement_name, measurement_value):
    """Helper function to create a measurement in the format InfluxDB expects."""
    return {
        "measurement": measurement_name,
        "tags": {
            "name": sensor_name,
        },
        "time": datetime.fromtimestamp(measurement_time).isoformat(),
        "fields": {
            "value": measurement_value
        }
    }


@dataclass
class Sensor:
    name: str
    unix_time: int
    temperature: float
    humidity: float
    battery: float

    @classmethod
    def from_json(cls, json_data):
        return cls(json_data["name"], json_data["timestamp"] // 1000000000,
                   float(json_data["temperature"]), float(json_data["humidity"]), float(json_data["battery"]))
    
    def influx_points(self):
        return [measurement_json(self.name, self.unix_time, "temperature", self.temperature),
                measurement_json(self.name, self.unix_time, "humidity", self.humidity),
                measurement_json(self.name, self.unix_time, "battery", self.battery)]

    
def parse_aiven_influx_dsn(url):
    o = urlparse(url)
    assert o.scheme == "https+influxdb"
    return dict(host=o.hostname, port=o.port, db=o.path[1:], username=o.username, password=o.password, ssl=True)


app = Starlette(debug=True)
app.mount('/static', StaticFiles(directory='statics'), name='static')

templates = Jinja2Templates(directory='templates')


@app.route('/')
async def homepage(request):
    return templates.TemplateResponse("index.html", {"request": request})
    #return RedirectResponse(url='/sensor/a4:c1:38:a7:a0:67?name=Office')


@app.route('/sensor/{sensor_id}')
async def sensor(request):
    if not re.match(r"[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}", request.path_params['sensor_id']):
        return ErrorResponse(HTTTPStatus.BAD_REQUEST)
    context = {
        "request": request,
        "sensor_id": request.path_params['sensor_id'],
        "sensor_name": request.query_params.get("name", request.path_params['sensor_id'])
    }
    return templates.TemplateResponse("index.html", context)


@app.route('/submit', methods=["POST"])
async def submit(request):
    print(request.headers['content-type'])
    if request.headers['content-type'] != "application/json":
        return ErrorResponse(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
    if not (sensor := Sensor.from_json(await request.json())):
        return ErrorResponse(HTTPStatus.BAD_REQUEST)
    async with InfluxDBClient(**parse_aiven_influx_dsn(AIVEN_INFLUX_DSN)) as client:
        print("Sending to Influx:", sensor.influx_points())
        await client.write(sensor.influx_points())    
    return JSONResponse({})


TEMPERATURE_QUERY = '''
select mean("value") as "mean_temperature" from temperature
where time > now()-48h and "name" = '%s'
group by time(15m)
order by time desc
limit 192
'''


@app.route('/data/{device_id}')
async def data(request):
    if not re.match(r"[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}", request.path_params['device_id']):
        return ErrorResponse(HTTTPStatus.BAD_REQUEST)
    async with InfluxDBClient(**parse_aiven_influx_dsn(AIVEN_INFLUX_DSN)) as client:
        resp = await client.query(TEMPERATURE_QUERY % (request.path_params['device_id'],))
        return JSONResponse(resp)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=15000)
