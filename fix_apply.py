from util import get_version, restart_component
import os
import sys
from distutils.dir_util import copy_tree
import logging

GENESIS_PATH = "C:\\edeploypos\\genesis\\src\\"
DATA_PATH = "C:\\edeploypos\\src\\"
BASE_UPDATE_FILE = os.path.dirname(os.path.realpath(__file__))
UPDATE_DIRS = [GENESIS_PATH, DATA_PATH]

def main_fix():
    version = get_version()
    execute(version, "update")

def execute(version, action):
    component_res = None
    #print("Updating files for version: {} with: {}".format(version, action))
    if version == "CORE:4.2.0|SRC:25.08.11":
        update_dir = os.path.join(BASE_UPDATE_FILE, "repository\\25.08.11\\{}".format(action))
        for component in os.listdir(update_dir):
            copy_update_file(os.path.join(update_dir, component), component)
            restart_component(component)
            return

    elif version == "CORE:5.2.3|SRC:26.02.11":
        update_dir = os.path.join(BASE_UPDATE_FILE, "repository\\26.02.11\\{}".format(action))
        for component in os.listdir(update_dir):
            copy_update_file(os.path.join(update_dir, component), component)
            component_res = component
            restart_component(component_res)
            return

    elif version == "CORE:5.2.6|SRC:26.05.06":
        update_dir = os.path.join(BASE_UPDATE_FILE, "repository\\26.05.06\\{}".format(action))
        for component in os.listdir(update_dir):
            copy_update_file(os.path.join(update_dir, component), component)
            component_res = component
            restart_component(component_res)
            return
    elif version == "CORE:5.3.0|SRC:26.05.12":
        update_dir = os.path.join(BASE_UPDATE_FILE, "repository\\26.05.12\\{}".format(action))
        for component in os.listdir(update_dir):
            copy_update_file(os.path.join(update_dir, component), component)
            component_res = component
            restart_component(component_res)
            return
    else:
        raise ValueError("Version already has the fix : {}, abort".format(version))


    #print("Process done")
    
def remove_pyc_files(file):
    for root, dirs, files in os.walk(file):
        for f in files:
            if f.endswith(".pyc"):
                os.remove(os.path.join(root, f))


def copy_update_file(file, component):
    for d in UPDATE_DIRS:
        f = os.path.join(d, component)
        if DATA_PATH in d:
            remove_pyc_files(f)

        copy_tree(file, f)

