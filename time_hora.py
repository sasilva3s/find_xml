# -*- coding: utf-8 -*-
from datetime import datetime
import time

def datetime_to_float(data_hora):
    dt_string = data_hora
    dt_object = datetime.strptime(dt_string, "%Y-%m-%d %H:%M:%S.%f")
    dt_object = dt_object.replace(second=0, microsecond=0)
    timestamp = time.mktime(dt_object.timetuple()) + (dt_object.microsecond / 1e6)
    return int(timestamp)
