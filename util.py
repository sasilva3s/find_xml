# -*- coding: utf-8 -*-
import logging
import sqlite3
import os
import time

POS_UPDATER_PATH = "C:\\edeploypos\\data\\server\\databases\\pos_updater.db"
RESTART_COMMAND = "C:\\edeploypos\\bin\\hv -o"

import glob

def get_version():
    connection = None
    query = \
        """select updatename from updatescontroller where typeid = 5 and notifieddate is not null order by notifieddate desc limit 1"""
    try:
        connection = sqlite3.connect(POS_UPDATER_PATH)
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()
        cursor.execute(query)
        return cursor.fetchone()[0]
    except Exception:
        logging.info("Unable to get system version")
    finally:
        if connection:
            connection.close()

def restart_component(component):
    if component == "remoteorder":
        component = "remoteorder"
    if component == "fiscalwrapper":
        component = "fiscalwrapper"
    os.system("{} {} >NUL".format(RESTART_COMMAND, component))
    logging.info("Component restarted: {}".format(component))
    time.sleep(2)



