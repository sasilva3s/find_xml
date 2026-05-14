# -*- coding: utf-8 -*-
import os
import base64
def get_xmls_list(dir_name):
    dirs_list = os.listdir(dir_name)
    all_files = list()

    for entry in dirs_list:
        full_path = os.path.join(dir_name, entry)

        if os.path.isdir(full_path):
            all_files = all_files + get_xmls_list(full_path)
        else:
            all_files.append(full_path)

    return all_files
