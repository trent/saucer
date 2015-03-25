import click
import os
import shutil
import sys
import sysconfig
import time

from platter import FORMATS, Log, make_spec, get_default_wheel_cache, Builder as PlatterBuilder, cli, clean_cache_cmd


INSTALLER = '''\
#!/bin/bash
# This script installs the bundled wheel distribution of %(name)s into
# a provided path where it will end up in a new virtualenv.

set -e

show_usage() {
echo "Usage: ./install.sh [OPTIONS] DST"
}

show_help() {
  show_usage
cat << EOF

  Installs %(name)s into a new virtualenv that is provided as the DST
  parameter.  The interpreter to use for this virtualenv can be
  overridden by the "-p" parameter.

Options:
  --help              display this help and exit.
  -p --python PYTHON  use an alternative Python interpreter
EOF
  exit 0
}

param_error() {
  show_usage
  echo
  echo "Error: $1"
  exit 1
}

py="%(python)s"

while [ "$#" -gt 0 ]; do
  case $1 in
    --help)         show_help ;;
    -p|--python)
      if [ "$#" -gt 1 ]; then
        py="$2"
        shift
      else
        param_error "$1 option requires an argument"
      fi
      ;;
    --python=?*)    py=${1#*=} ;;
    --)             shift; break ;;
    -?*)            param_error "no such option: $1" ;;
    *)              break
  esac
  shift
done

if [ "$1" == "" ]; then
  param_error "destination argument is required"
fi

HERE="$(cd "$(dirname "$0")"; pwd)"
DATA_DIR="$HERE/data"
SRC_DIR="$HERE/src"

# Ensure Python exists
command -v "$py" &> /dev/null || error "Given python interpreter not found ($py)"

echo 'Setting up virtualenv'
"$py" "$DATA_DIR/virtualenv.py" "$1"
VIRTUAL_ENV="$(cd "$1"; pwd)"

INSTALL_ARGS=''
if [ -f "$DATA_DIR/requirements.txt" ]; then
  INSTALL_ARGS="$INSTALL_ARGS"\ -r\ "$DATA_DIR/requirements.txt"
fi

echo "Copying %(name)s"
cp -R "$SRC_DIR" "$VIRTUAL_ENV"

# Potential post installation
cd "$HERE"
. "$VIRTUAL_ENV/bin/activate"
%(postinstall)s

echo "Done."
'''

class Builder(PlatterBuilder):
    def describe_package(self, python):
        # # Do dummy invoke first to trigger setup requires.
        # self.log.info('Invoking dummy setup to trigger requirements.')
        # self.execute(python, ['setup.py', '--version'], capture=True)

        # rv = self.execute(python, [
        #     'setup.py', '--name', '--version', '--fullname'],
        #     capture=True).strip().splitlines()
        name = os.path.basename(self.path)
        version = 'version'
        # commit = self.execute('git', ['rev-parse', 'HEAD']).strip().splitlines()[0]
        # branch = self.execute('git', ['rev-parse', '--abbrev-ref', 'HEAD']).strip().splitlines()[0]
        # tree = self.tree
        platform = sysconfig.get_platform()
        return {
            'name': name,
            'version': version,
            'platform': platform,
            'ident': '{}-{}-ident'.format(name, version),
        }

    def build_wheels(self, venv_path, data_dir):
        self.log.info('Building wheels')
        pip = os.path.join(venv_path, 'bin', 'pip')

        with self.log.indented():
            self.execute(pip, ['install', '--download', data_dir] +
                         self.get_pip_options() +
                         [make_spec('wheel', self.wheel_version)])

            cmdline = ['wheel', '--wheel-dir=' + data_dir]
            cmdline.extend(self.get_pip_options())

            if self.requirements is not None:
                cmdline.extend(('-r', self.requirements))
                shutil.copy2(self.requirements,
                             os.path.join(data_dir, 'requirements.txt'))

            # cmdline.append(self.path)

            self.execute(os.path.join(venv_path, 'bin', 'pip'), cmdline)

    def put_source(self, scratchpad):
        dest = os.path.join(scratchpad, 'src')
        self.log.info('Copying source {} to distribution at {}', self.path, dest)
        shutil.copytree(self.path, dest, ignore=shutil.ignore_patterns('dist', '.git*', '.DS_Store', '*.pyc'))

    def put_installer(self, scratchpad, pkginfo, install_script_path):
        fn = os.path.join(scratchpad, 'install.sh')

        with open(install_script_path) as f:
            postinstall = f.read().rstrip().decode('utf-8')

        with open(fn, 'w') as f:
            f.write((INSTALLER % dict(
                name=pkginfo['ident'],
                pkg=pkginfo['name'],
                python=os.path.basename(self.python),
                postinstall=postinstall,
            )).encode('utf-8'))
        os.chmod(fn, 0100755)

    def build(self, format, prebuild_script=None, postbuild_script=None):
        if not os.path.isdir(self.path):
            raise click.UsageError('The project path (%s) does not exist'
                                   % self.path)

        now = time.time()
        venv_src, venv_artifact = self.extract_virtualenv()

        venv_path = self.setup_build_venv(venv_src)
        local_python = os.path.join(venv_path, 'bin', 'python')

        self.log.info('Analyzing package')
        pkginfo = self.describe_package(local_python)
        with self.log.indented():
            self.log.info('Name: {}', pkginfo['name'])
            self.log.info('Version: {}', pkginfo['version'])

        scratchpad = self.make_scratchpad('buildbase')
        data_dir = os.path.join(scratchpad, 'data')
        os.makedirs(data_dir)

        install_script_path = os.path.join(venv_path, 'install_script')

        self.place_venv_deps(venv_src, data_dir)
        if prebuild_script is not None:
            self.run_build_script(scratchpad, venv_path, prebuild_script,
                                  install_script_path)

        self.build_wheels(venv_path, data_dir)
        self.put_meta_info(scratchpad, pkginfo)
        open(install_script_path, 'a').close()
        if postbuild_script is not None:
            self.run_build_script(scratchpad, venv_path, postbuild_script,
                                  install_script_path)

        if self.wheel_cache:
            self.update_wheel_cache(data_dir, venv_artifact)

        self.put_installer(scratchpad, pkginfo,
                           install_script_path)
        self.put_source(scratchpad)
        artifact = self.create_archive(scratchpad, pkginfo, format)

        self.cleanup()
        self.finalize(artifact, time.time() - now)

    # def build(self, format, prebuild_script=None, postbuild_script=None):
    #     if not os.path.isdir(self.path):
    #         raise click.UsageError('The project path (%s) does not exist'
    #                                % self.path)

    #     now = time.time()
    #     venv_src, venv_artifact = self.extract_virtualenv()

    #     venv_path = self.setup_build_venv(venv_src)
    #     local_python = os.path.join(venv_path, 'bin', 'python')

    #     self.log.info('Analyzing repo')
    #     repoinfo = self.describe_repo()
    #     with self.log.indented():
    #         self.log.info('Name: {}', pkginfo['name'])
    #         self.log.info('Version: {}', pkginfo['version'])

    #     scratchpad = self.make_scratchpad('buildbase')
    #     data_dir = os.path.join(scratchpad, 'data')
    #     src_dir = os.path.join(scratchpad, 'src')
    #     os.makedirs(data_dir)
    #     os.makedirs(src_dir)

    #     install_script_path = os.path.join(venv_path, 'install_script')

    #     self.place_venv_deps(venv_src, data_dir)
    #     if prebuild_script is not None:
    #         self.run_build_script(scratchpad, venv_path, prebuild_script,
    #                               install_script_path)

    #     self.build_wheels(venv_path, data_dir)
    #     self.put_meta_info(scratchpad, pkginfo)
    #     open(install_script_path, 'a').close()
    #     if postbuild_script is not None:
    #         self.run_build_script(scratchpad, venv_path, postbuild_script,
    #                               install_script_path)

    #     if self.wheel_cache:
    #         self.update_wheel_cache(data_dir, venv_artifact)

    #     self.put_installer(scratchpad, pkginfo,
    #                        install_script_path)
    #     artifact = self.create_archive(scratchpad, pkginfo, format)

    #     self.cleanup()
    #     self.finalize(artifact, time.time() - now)

@cli.command('build')
@click.argument('path', required=False, type=click.Path())
@click.option('--output', type=click.Path(), default='dist',
              help='The output folder', show_default=True)
@click.option('-p', '--python', type=click.Path(),
              help='The python interpreter to use for building.  This '
              'interpreter is both used for compiling the packages and also '
              'used as default in the generated install script.')
@click.option('--virtualenv-version', help='The version of virtualenv to use. '
              'The default is to use the latest stable version from PyPI.',
              metavar='SPEC')
@click.option('--pip-option', multiple=True, help='Adds an option to pip.  To '
              'add multiple options, use this parameter multiple times.  '
              'Example:  --pip-option="--isolated"',
              type=click.Path(), metavar='OPT')
@click.option('--wheel-version', help='The version of the wheel package '
              'that should be used.  Defaults to latest stable from PyPI.',
              metavar='SPEC')
@click.option('--format', default='tar.gz', type=click.Choice(FORMATS),
              help='The format of the resulting build artifact as file '
              'extension.  Supported formats: ' + ', '.join(FORMATS),
              show_default=True, metavar='EXTENSION')
@click.option('--prebuild-script', type=click.Path(),
              help='Path to an optional build script that is invoked in '
              'the build folder as first step.  This can be used to install '
              'build dependencies such as Cython.')
@click.option('--postbuild-script', type=click.Path(),
              help='Path to an optional build script that is invoked in '
              'the build folder as last step.  This can be used to inject '
              'additional data into the archive.')
@click.option('--wheel-cache', type=click.Path(),
              help='An optional folder where platter should cache wheels '
              'instead of the system default.  If you do not want to use '
              'a wheel cache you can pass the --no-wheel-cache flag.')
@click.option('--no-wheel-cache', is_flag=True,
              help='Disables the wheel cache entirely.')
@click.option('--no-download', is_flag=True,
              help='Disables the downloading of all dependencies entirely. '
              'This will only work if all dependencies have been previously '
              'cached.  This is primarily useful when you are temporarily '
              'disconnected from the internet because it will disable useless '
              'network roundtrips.')
@click.option('-r', '--requirements', type=click.Path(),
              help='Optionally the path to a requirements file which contains '
              'additional packages that should be installed in addition to '
              'the main one.  This can be useful when you need to pull in '
              'optional dependencies.')
def build_cmd(path, output, python, virtualenv_version, wheel_version,
              format, pip_option, prebuild_script, postbuild_script,
              wheel_cache, no_wheel_cache, no_download, requirements):
    """Builds a platter package.  The argument is the path to the package.
    If not given it discovers the closest setup.py.

    Generally this works by building the provided package into a wheel file
    and a wheel for each of the dependencies.  The resulting artifacts are
    augmented with a virtualenv bootstrapper and an install script and then
    archived.  Optionally a post build script can be provided that can place
    more files in the archive and also provide more install steps.
    """
    log = Log()
    if path is None:
        path = os.getcwd()
    log.info('Using project from {}', path)

    if no_wheel_cache:
        if no_download:
            raise click.UsageError('--no-download and --no-cache cannot '
                                   'be used together.')
        wheel_cache = None
    elif wheel_cache is None:
        wheel_cache = get_default_wheel_cache()
    if wheel_cache is not None:
        log.info('Using wheel cache in {}', wheel_cache)

    with Builder(log, path, output, python=python,
                 virtualenv_version=virtualenv_version,
                 wheel_version=wheel_version,
                 pip_options=list(pip_option),
                 no_download=no_download,
                 wheel_cache=wheel_cache,
                 requirements=requirements) as builder:
        builder.build(format, prebuild_script=prebuild_script,
                      postbuild_script=postbuild_script)