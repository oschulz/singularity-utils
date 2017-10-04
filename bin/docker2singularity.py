#!/usr/bin/env python

# -*- coding: utf-8 -*-
#
# Copyright (C) 2016 Oliver Schulz <oschulz@mpp.mpg.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


import logging
import argparse
import os, shutil
from os import mkdir, path
import re
from glob import glob
from tempfile import mkdtemp, mktemp
import subprocess
from subprocess import Popen, PIPE, call, check_output
import distutils.spawn
import json
import codecs
from string import Template


def shell_double_quote(s):
    return '"' + s.replace('"', '\\\"') + "\""

def shell_single_quote(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"

def docker_env_entry_trafo(s):
    name, value = s.split('=', 1)
    return {'name': name, 'value': value}    

def prepend_env_var(name, path):
    if not ("$"+name+":" in path) or ("${"+name+"}:" in path):
        return "$"+name+":" + path
    else:
        return path

def env_elem_subst(e):
    # if (e['name'] == "PATH"): e['value'] = prepend_env_var("PATH", e['value'])
    return e

def print_to_file(filename, mode, s):
    with open(filename, "w") as f:
        f.write(s)
    os.chmod(filename, mode)

def print_to_file_if_not_exists(filename, mode, s):
    if (not path.isfile(filename)):
        with open(filename, "w") as f:
            f.write(s)
        os.chmod(filename, mode)
    else:
        logging.warn("Keeping existing \"%s\".", filename)


tmp_area = ""

def clean_up():
    if (tmp_area != ""):
        logging.info("Deleting temporary directory \"%s\".", tmp_area)
        shutil.rmtree(tmp_area)

def error_exit(*args, **kwargs):
    # if (tmp_area != ""):
    #     logging.warn("Aborting, but will not delete temporary directory \"%s\".", tmp_area)
    clean_up()
    logging.error(*args, **kwargs)
    exit(1)

def singularity_libexecdir():
    singularity_exe = distutils.spawn.find_executable("singularity")
    if not singularity_exe:
        error_exit("Can't find singularity executable")
    else:
        proc = subprocess.Popen(['strings', singularity_exe], stdout=subprocess.PIPE)

        keyvals = {}
        for line in proc.stdout.readlines():
            m = re.match(r'^(\w+)="(.*)"\s*$', line)
            if m:
                keyvals[m.group(1)] = m.group(2)

        libexecdir = keyvals["libexecdir"]
        while "$" in libexecdir:
            libexecdir = Template(libexecdir).substitute(keyvals)
    if libexecdir:
        return libexecdir
    else:
        error_exit("Can't determine singularity's libexecdir")


logging.basicConfig(level = logging.INFO, format = "%(levelname)s: %(message)s")
logging.info('docker2singularity started')

parser = argparse.ArgumentParser()
parser.add_argument("input", help="Docker image or container name")
parser.add_argument("output", help="Singularity container image path")
parser.add_argument("-u", "--unprivileged", help="unprivileged mode (uses docker2aci)", action="store_true")
parser.add_argument("-a", "--add", help="contents to add to output image (via rsync)", action="append")
args = parser.parse_args()

logging.info('input = %s', args.input)
logging.info('output = %s', args.output)
logging.info('unprivileged = %r', args.unprivileged)
logging.info('add = %s', str(args.input))


output_filename = re.sub("/+$", "", args.output)
if not output_filename:
    error_exit("Invalid output name \"%s\"", args.output)

output_basename = path.basename(output_filename)
output_dirname = path.dirname(output_filename)
output_dirname = path.abspath(output_dirname) if output_dirname else os.getcwd()
output_noext, output_ext = os.path.splitext(output_basename)

logging.info('output_ext = %s', output_ext)

if (output_ext == ""):
    output_type = "directory"
elif (output_ext == ".sqsh"):
    output_type = "SquashFS"
else:
    error_exit("Unkown output path extension \"%s\"", output_ext)

logging.info('Output type: %s', output_type)

if (not path.isdir(output_dirname)):
    error_exit("Can't create output in \"%s\", doesn't exist or not a directory", output_filename)

if (output_type == "directory"):
    if (path.exists(output_filename)):
        error_exit("Output directory \"%s\" already exists", output_filename)

    tmp_area = mkdtemp(prefix = output_basename + "-", dir = output_dirname)
else:
    tmp_area = mkdtemp(prefix = "docker2singularity-")

logging.info("Temporary work area: \"%s\"", tmp_area)



rootfs_dir = "/dev/null"
env_vars = []
run_cmd = []

if (args.unprivileged):
    input_url = "docker://" + args.input
    p = Popen(["docker2aci", input_url], cwd = tmp_area)
    p.communicate()
    aci_file = glob(os.path.join(tmp_area, "*.aci"))[0]
    p = Popen(["tar", "--exclude=dev", "-x", "-f", path.basename(aci_file)], cwd = tmp_area)
    p.communicate()
    os.remove(aci_file)
    rootfs_dir = path.join(tmp_area, "rootfs")

    aci_manifest_file = os.path.join(tmp_area, "manifest")
    with codecs.open(aci_manifest_file, encoding='utf-8') as f:
        aci_manifest = json.load(f)
    app_data = aci_manifest.get('app', {})
    app_data = app_data if app_data else {}
    env_data = app_data.get('environment', None)
    if isinstance(env_data, list): env_vars.extend(env_data)
    run_cmd_data = app_data.get('exec', None)
    if isinstance(run_cmd_data, list): run_cmd.extend(run_cmd_data)
else:
    image = args.input
    logging.info(["docker", "run", "-d", image, "/bin/sh"])
    container = ""
    try:
        inspect_str = check_output(["docker", "inspect", image])
        inspect_data = json.loads(inspect_str)[0]
        config_data = inspect_data.get('Config', {})
        config_data = config_data if config_data else {}
        env_data = config_data.get('Env', None)
        if isinstance(env_data, list): env_vars.extend([docker_env_entry_trafo(s) for s in env_data])
        run_cmd_data = config_data.get('Cmd', None)
        if isinstance(run_cmd_data, list): run_cmd.extend(run_cmd_data)

        container = check_output(["docker", "run", "-d", image, "/bin/sh"]).strip()
        logging.info("Started container %s from %s", container, image)
        call(["docker", "stop", "--time=1", container])
        logging.info("Stopped container %s", container)

        rootfs_dir = path.join(tmp_area, "rootfs")
        os.mkdir(rootfs_dir)
        p1 = Popen(["docker", "export", container], stdout = PIPE)
        p2 = Popen(["tar", "--exclude=dev", "-x", "-f", "-"], cwd = rootfs_dir, stdin = p1.stdout)
        p1.stdout.close()  # Allow p1 to receive a SIGPIPE if p2 exits.
        p2.communicate()[0]
    finally:
        if container:
            call(["docker", "rm", container])
            logging.info("Removed container %s", container)


if not path.isdir(path.join(rootfs_dir, "dev")):
    os.mkdir(path.join(rootfs_dir, "dev"))

if path.isfile(path.join(rootfs_dir, ".dockerenv")):
    os.remove(path.join(rootfs_dir, ".dockerenv"))


libexecdir = singularity_libexecdir()
environment_tar_path = path.join(libexecdir, "singularity", "bootstrap-scripts", "environment.tar")
print path.isfile(environment_tar_path)
logging.info("Extracting %s to %s", environment_tar_path, rootfs_dir)
subprocess.call(["tar", "-x", "-v", "-C", rootfs_dir, "-f", environment_tar_path])


env_var_names = [e['name'] for e in env_vars]
if not 'PATH' in env_var_names:
    env_vars.insert(0, {'name': 'PATH', 'value': "/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin"})
if not 'LD_LIBRARY_PATH' in env_var_names:
    env_vars.insert(1, {'name': 'LD_LIBRARY_PATH', 'value': ""})

env_vars = [env_elem_subst(e) for e in env_vars]

logging.info("Singularity container environment variables:")
for e in env_vars:
    logging.info("%s=%s", e['name'], shell_double_quote(e['value']))

quoted_run_cmd = ""
if run_cmd:
    quoted_run_cmd = " ".join([shell_double_quote(x) for x in run_cmd])
    logging.info("Singularity container run cmd: %s.", quoted_run_cmd)
else:
    logging.info("Singularity container has no default run cmd.")

environment_contents = ""
environment_contents += "\n".join(["export " + e['name'] + "=" + shell_double_quote(e['value']) for e in env_vars]) + "\n"
print_to_file(path.join(rootfs_dir, ".singularity.d", "env", "10-docker.sh"), 0644, environment_contents)


subprocess.call(["sed", "s/bash --norc/bash/", "-i", path.join(rootfs_dir, ".singularity.d", "actions", "shell")])


logging.info("RUN CMD: \"%s\"", quoted_run_cmd)
if quoted_run_cmd:
    runscript_contents = "#!/bin/sh\n" + "\n"
    runscript_contents += "exec " + quoted_run_cmd + "\n"
    print_to_file(path.join(rootfs_dir, ".singularity.d", "runscript"), 0755, runscript_contents)


#TODO: Support for -a option #!!!

if (output_type == "directory"):
    shutil.move(rootfs_dir, output_filename)
elif (output_type == "SquashFS"):
    subprocess.call(['chmod', '-R', 'u+rwX,go+rX', rootfs_dir])
    output_noext, output_ext
    tmp_output_filename = mktemp(prefix=output_noext+"_tmp-", suffix=output_ext, dir=output_dirname)
    subprocess.call(["mksquashfs", rootfs_dir, tmp_output_filename, "-all-root"])
    os.rename(tmp_output_filename, output_filename)
else:
    error_exit("Internal error, unkown output type \"%s\"", SquashFS)

clean_up()
logging.info('docker2singularity done.')
