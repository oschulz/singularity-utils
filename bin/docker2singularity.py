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
from glob import glob
from tempfile import mkdtemp
import subprocess
from subprocess import Popen, PIPE, call, check_output
import json
import codecs


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


output_filename = args.output
output_basename = path.basename(output_filename)
output_dirname = path.dirname(output_filename)
output_noext, output_ext = os.path.splitext(output_filename)

logging.info('output_ext = %s', output_ext)

if (output_ext == ""):
    output_type = "directory"
elif (output_ext == ".sqsh"):
    output_type = "SquashFS"
else:
    error_exit("Unkown output path extension \"%s\"", output_ext)

logging.info('Output type: %s', output_type)

if (path.exists(output_filename)):
    error_exit("Output \"%s\" already exists", output_filename)

if (not path.isdir(output_dirname)):
    error_exit("Can't create output in \"%s\", doesn't exist or not a directory", output_filename)

if (output_type == "directory"):
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
    env_data = aci_manifest['app']['environment']
    if isinstance(env_data, list): env_vars.extend(env_data)
    run_cmd_data = aci_manifest['app']['exec']
    if isinstance(run_cmd_data, list): run_cmd.extend(run_cmd_data)
else:
    image = args.input
    logging.info(["docker", "run", "-d", image, "/bin/sh"])
    container = ""
    try:
        inspect_str = check_output(["docker", "inspect", image])
        inspect_data = json.loads(inspect_str)[0]
        env_data = inspect_data['Config']['Env']
        if isinstance(env_data, list): env_vars.extend([docker_env_entry_trafo(s) for s in env_data])
        run_cmd_data = inspect_data['Config']['Cmd']
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

env_vars.append({'name': 'PS1', 'value': "Singularity.$SINGULARITY_CONTAINER> $PS1"})
env_vars.append({'name': 'SINGULARITY_INIT', 'value': "1"})

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

environment_contents = """\
# Define any environment init code here

if test -z "$SINGULARITY_INIT"; then
"""
environment_contents += "    "+ "\n    ".join([e['name'] + "=" + shell_double_quote(e['value']) for e in env_vars]) + "\n"
environment_contents += "    export " + " ".join(env_var_names + ["PS1", "SINGULARITY_INIT"]) + "\n"
environment_contents += """\
fi
"""
print_to_file_if_not_exists(path.join(rootfs_dir, "environment"), 0644, environment_contents)


shell_path = "/bin/bash" if path.isfile(path.join(rootfs_dir, "bin", "bash")) else "/bin/sh"
logging.info("Using %s as default shell for container.", shell_path)


#dot_shell_contents = "#!" + shell_path + "\n"
#dot_shell_contents += """\
#. /environment
#if test -n "$SHELL" -a -x "$SHELL"; then
#    exec "$SHELL" "$@"
#else
#    echo "ERROR: Shell does not exist in container: $SHELL" 1>&2
#    echo "ERROR: Using /bin/sh instead..." 1>&2
#fi
#if test -x /bin/sh; then
#    SHELL=/bin/sh
#    export SHELL
#    exec /bin/sh "$@"
#else
#    echo "ERROR: /bin/sh does not exist in container" 1>&2
#fi
#exit 1
#"""
#
dot_shell_contents = "#!" + shell_path + "\n" + "\n"
dot_shell_contents += """\
. /environment
"""
dot_shell_contents += "SHELL=" + shell_double_quote(shell_path) + "\n"
dot_shell_contents += """\
export SHELL
if test -n "$SHELL" -a -x "$SHELL"; then
    exec "$SHELL" "$@"
else
    echo "ERROR: Shell does not exist in container: $SHELL" 1>&2
fi
exit 1
"""
print_to_file_if_not_exists(path.join(rootfs_dir, ".shell"), 0755, dot_shell_contents)


dot_exec_contents = "#!" + shell_path + "\n"
dot_exec_contents += """\
. /environment
exec "$@"
"""
print_to_file_if_not_exists(path.join(rootfs_dir, ".exec"), 0755, dot_exec_contents)


dot_run_contents = "#!" + shell_path + "\n"
dot_run_contents += """\
. /environment
if test -x /singularity; then
    exec /singularity "$@"
else
    echo "No Singularity runscript found, executing /bin/sh"
    exec /bin/sh "$@"
fi
"""
print_to_file_if_not_exists(path.join(rootfs_dir, ".run"), 0755, dot_run_contents)

logging.info("RUN CMD: \"%s\"", quoted_run_cmd)
if quoted_run_cmd:
    singularity_contents = "#!" + shell_path + "\n" + "\n"
    singularity_contents += "exec " + quoted_run_cmd + "\n"
    print_to_file_if_not_exists(path.join(rootfs_dir, "singularity"), 0755, singularity_contents)


#TODO: Support for -a option #!!!

if (output_type == "directory"):
    shutil.move(rootfs_dir, output_filename)
elif (output_type == "SquashFS"):
    subprocess.call(["mksquashfs", rootfs_dir, output_filename, "-all-root"])
else:
    error_exit("Internal error, unkown output type \"%s\"", SquashFS)

clean_up()
logging.info('docker2singularity done.')
