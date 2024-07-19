#!/usr/bin/env python3.12
# -*- coding: utf-8 -*-
import argparse
import csv
import logging
import pathlib
import pprint
import shutil
import subprocess
import sys
import tempfile
import typing

import jinja2

_LOG_FORMAT = "%(levelname)-7s %(asctime)s: %(message)s"
logging.basicConfig(stream=sys.stdout, format=_LOG_FORMAT)
logger = logging.getLogger()
logger.setLevel(logging.WARN)

ConfigDict = typing.Dict[str, dict[str, str]]


class CustomHelpFormatter(argparse.HelpFormatter):
    def format_help(self):
        original_help = super().format_help()
        return original_help + "  --\t\t\tkustomize command overide \n"


class TempDir:
    """
    Represents a dir which is intended to be ephemeral.  Intended to be used as an `argparse`
    argument type.

    Usage:
    ```
    # specify a temporary directory
    t = TempDir('foo/')
    t()         # creates temp dir
    t.cleanup() # all done

    # use system default w/ context manager:
    with TempDir():
      # do stuff
    ```
    """
    _temp: typing.Optional[tempfile.TemporaryDirectory]

    def __init__(self, path: typing.Optional[str] = None):
        if path is None:
            self._temp = tempfile.TemporaryDirectory()
            self.path = pathlib.Path(self._temp.name).resolve()
        else:
            self._temp = None
            self.path = pathlib.Path(path).resolve()
            if self.path.exists():
                raise argparse.ArgumentTypeError(
                    f"provided directory '{self.path}' already exists")

    def __call__(self, *args, **kwargs):
        self.path.mkdir(parents=True, exist_ok=True)
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def cleanup(self):
        if self._temp is not None:
            self._temp.cleanup()
        else:
            shutil.rmtree(self.path, ignore_errors=True)


class LazyFileType(argparse.FileType):
    """
    Subclasses `argparse.FileType` in order to provide a way to lazily open
    files for reading/writing from arguments.  Initializes the same as the
    parent, but provides `open` method which returns the file object.

    Usage:
    ```
    parser = argparse.ArgumentParser()
    parser.add_argument('f', type=LazyFileType('w'))
    args = parser.parse_args()

    with args.f.open() as f:
        for line in foo:
            ...
    ```

    Provides an alternate constructor for use with the `default` kwarg to
    `ArgumentParser.add_argument`.

    Usage:
    ```
    #
    parser.add_argument('-f', type=LazyFileType('w'),
                        default=LazyFileType.default('some_file.txt')
    """

    def __call__(self, string: str) -> typing.Self:
        self.filename = string

        if 'r' in self._mode or 'x' in self._mode:
            if not pathlib.Path(self.filename).exists():
                m = (f"can't open {self.filename}:  No such file or directory: "
                     f"'{self.filename}'")
                raise argparse.ArgumentTypeError(m)

        return self

    def open(self) -> typing.IO:
        return open(self.filename, self._mode, self._bufsize, self._encoding,
                    self._errors)

    @classmethod
    def default(cls, string: str, **kwargs) -> typing.Self:
        inst = cls(**kwargs)
        inst.filename = string
        return inst


def is_jinja_template(dest_file: pathlib.Path) -> bool:
    """
    Check if file is a jinja template
    :param dest_file: file to check
    :return: bool indicating if jinja template or not
    """
    return dest_file.suffix == '.j2'


def setup_jinja(template_path: pathlib.Path) -> jinja2.Environment:
    """
    Setup jinja environment and return
    :param template_path: path to use for jinja loader `jinja2.FileSystemLoader`
    :return: `jinja2.Environment`
    """
    logger.debug(f'Setting up Jinja with template loader path: {template_path}')
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_path),
        autoescape=True
    )


def template_file(template_file: pathlib.Path, cluster_config: dict, jinja_env: jinja2.Environment,
                  delete_template: bool = False):
    """
    Jinja template a file to the same name, minus a `.j2` extension, and optionally delete when
    complete
    :param template_file: path to template file (should be a Jinja template with a `.j2` extension)
    :param cluster_config: cluster config dict
    :param jinja_env: jinja environment to search for template in `template_file`
    :param delete_template: whether to delete original template file
    :return: None
    """
    # compute relative path from Jinja environment template loader path to source template
    loader_path = pathlib.Path(jinja_env.loader.searchpath[0]).resolve()
    template_relative_path = template_file.relative_to(loader_path)
    logger.debug(f'Template relative path: {template_relative_path}')

    try:
        template = jinja_env.get_template(str(template_relative_path))
    except jinja2.exceptions.TemplateError as e:
        logger.exception(f'Error getting template {template_file}')
        raise RuntimeWarning('Error getting template') from e
    file_path = template_file.parent.joinpath(template_file.stem)
    logger.info(f"Rendering template '{template_file.name}' to {file_path}")
    with open(file_path, 'w') as f:
        try:
            f.write(template.render(**cluster_config))
        except jinja2.exceptions.TemplateError as e:
            logger.exception(f'Error rendering template {template_file}')
            raise RuntimeWarning('Error rendering template') from e
        logger.debug(f'Wrote {f.tell()} bytes to {file_path}')

    if delete_template is True:
        logger.info(f"Deleting template '{template_file.name}'")
        template_file.unlink()


def copy_and_template(base_dir: pathlib.Path, overlay_dir: pathlib.Path, *, dest_dir: pathlib.Path,
                      cluster_config: dict, jinja_env: jinja2.Environment, template: bool = True):
    """
    Recursively walks sources (base and overlay dirs), creating an identical directory structure
    in `dest_dir`, and copies files from sources to dest.  If Jinja templates are encountered, they
    will be rendered in `dest_dir` if `template` is True.
    :param base_dir: path to base directory
    :param overlay_dir: path to overlay directory
    :param dest_dir: destination directory for copying files (temp/scratch dir)
    :param cluster_config: configuration dict
    :param jinja_env: jinja environment to use for template rendering
    :param template: whether to render encountered Jinja templates
    :return: None
    """

    def generate(b, o):
        logger.debug(f'Traversing source base path: {b}')
        yield from b.walk()
        logger.debug(f'Traversing source overlay path: {o}')
        yield from o.walk()

    for (root, dirs, files) in generate(base_dir, overlay_dir):
        files = (root.joinpath(pathlib.Path(file)) for file in files)

        # compute relative path from src root to copy into dest
        try:
            next_dir = dest_dir.joinpath(root.relative_to(base_dir.parent))
        except ValueError:
            next_dir = dest_dir.joinpath(root.relative_to(overlay_dir.parent))

        if not next_dir.exists():
            logger.debug(f"Creating directory: {next_dir}")
            next_dir.mkdir(parents=True, exist_ok=True)

        for file in files:
            dest_file = next_dir.joinpath(file.name)
            logger.debug(f'Copying {file} to {dest_file}')
            shutil.copy2(file, dest_file)
            if template is True and is_jinja_template(dest_file):
                template_file(template_file=dest_file,
                              cluster_config=cluster_config,
                              jinja_env=jinja_env,
                              delete_template=True)

    logger.debug('Done copying templates')


def process_sot_file(sot_f: typing.IO) -> ConfigDict:
    """
    Takes a file-like object; returns a dictionary where the key is the cluster name, the value is
    a dict with the cluster's configuration
    :param sot_f: file to process
    :raises RuntimeError: any failed check raises RuntimeError; all other exceptions are unexpected
    :return: parsed config as dict
    """
    logger.debug(f"Processing source of truth file: {sot_f.name}")
    reader = csv.DictReader(sot_f, dialect='excel')
    data: ConfigDict = {}
    row: dict[str, str]
    for row in reader:
        row = {k.strip(): v.strip() for k, v in row.items() if row}
        try:
            data[row['cluster_name']] = row
        except KeyError as e:
            logger.error("Source of truth file missing 'cluster_name' column")
            logger.debug(f'Got CSV: {row}')
            raise RuntimeError from e
    return data


def run_kustomize(*, output_dir: pathlib.Path, overlay_dir: pathlib.Path,
                  cluster_config: dict[str, str],
                  command_override: typing.Optional[list] = None) -> None:
    """
    Runs kustomize using `subprocess.Popen` and dumps the output to a specified location.  Pipes
    stderr to stdout; writes stdout to logging facility as loglevel `info`.
    :param output_dir: directory to write the kustomize-generated template
    :param overlay_dir: directory where overlay has been copied and templated (becomes kustomize
        cwd)
    :param cluster_config: cluster config as dict
    :param command_override: command override to kustomize; if not specified, a default is used
    :raise RuntimeError: if any issues are encountered at runtime; other exceptions are unexpected
    :return: None
    """
    filename = f'{cluster_config['cluster_name']}.yaml'

    if command_override is None or command_override == []:
        command_override = ["kustomize", "build", ".", "-o",
                            str(output_dir.joinpath(filename).resolve())]

    orig_command = command_override[0]
    full_path_to_cmd = shutil.which(orig_command)
    if full_path_to_cmd is None:
        err = f'Could not find {command_override[0]} in the path'
        logger.error(err)
        raise RuntimeError(err)

    command_override[0] = full_path_to_cmd

    logger.debug(f"Running '{" ".join(command_override)}'")
    logger.info(f'Running {full_path_to_cmd}')
    with subprocess.Popen(command_override, bufsize=1, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, cwd=overlay_dir) as p:
        while True:
            stdout = p.stdout.read().strip('\n')
            if stdout:
                logger.info(f'{orig_command}: {stdout}')

            # check to see if the process is complete and log status if so
            # if not complete, continue the loop
            if p.poll() is None:
                continue
            elif p.returncode == 0:
                logger.info(
                    f'{full_path_to_cmd} completed successfully with exitcode {p.returncode}')
            elif p.returncode > 0:
                logger.error(f'{full_path_to_cmd} exited with {p.returncode}')

            break


def process_cluster(args: argparse.Namespace, config: dict[str, str],
                    jinja_env: jinja2.Environment,
                    output_subdir: 'str' = 'none') -> None:
    """
    Process a given cluster using its config
    :param args: parsed command line arguments
    :param config: cluster configuration as dict
    :param jinja_env: jinja2 environment to use to render templates
    :param output_subdir: whether to generate a kustomize manifest subdirectory per
        cluster
    :return: None
    """

    group: str = config['cluster_group']
    clus_overlay_dir = args.overlay.joinpath(group)
    if not clus_overlay_dir.exists():
        logger.warning(f"Cluster '{config['cluster_name']}': missing overlay for group '{group}';"
                       f" nothing to hydrate")
        raise RuntimeWarning('No overlay')

    # walk the sources (base and overlay), copy to temp dir, template if needed
    copy_and_template(base_dir=args.base,
                      overlay_dir=clus_overlay_dir,
                      dest_dir=args.temp.path,
                      cluster_config=config,
                      jinja_env=jinja_env)

    # setup hydration source and destination directories and ensure dest exists
    hydration_src = args.temp.path.joinpath(args.overlay.name).joinpath(config['cluster_group'])
    if output_subdir == 'cluster':
        hydrated_dest = args.hydrated.joinpath(config['cluster_name'])
    elif output_subdir == 'group':
        hydrated_dest = args.hydrated.joinpath(config['cluster_group'])
    else:  # is none
        hydrated_dest = args.hydrated

    hydrated_dest.mkdir(parents=True, exist_ok=True)

    # run kustomize in the temp dir for the cluster
    run_kustomize(output_dir=hydrated_dest,
                  overlay_dir=hydration_src,
                  cluster_config=config,
                  command_override=args.kustomize)


def check_config(cluster_config: dict) -> None:
    """
    Perform basic cluster config checks and raise an exception if anything is found
    :param cluster_config: cluster config as dict
    :raises RuntimeError: any failed check raises RuntimeError; all other exceptions are unexpected
    """
    if not cluster_config:
        logger.error(f'No config found for provided cluster name')
        raise RuntimeError
    else:
        logger.debug(f'Found config for cluster; config: \n{pprint.pformat(cluster_config)}')

    try:
        group: str = cluster_config['cluster_group']
        assert group.strip() != "", "cluster_group is required and should not be empty"
    except KeyError as e:
        logger.error(f"Could not find column 'cluster_group' in source")
        raise RuntimeError('Missing cluster_group') from e
    except AssertionError as e:
        logger.error(e)
        raise RuntimeError(e) from e

    try:
        cluster_config['cluster_tags']
    except KeyError:
        logger.error(f"Could not find column 'tags' in source")


def setup_logger(args):
    if args.quiet:
        logger.setLevel(logging.ERROR)
    elif args.verbose == 1:
        logger.setLevel(logging.INFO)
    elif args.verbose >= 2:
        logger.setLevel(logging.DEBUG)


def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments.  Normalizes some arguments, specifically provided
    `pathlib.Path`s.  Performs some argument validation and raises an error if any of the checks
    fail while logging errors to logging facility
    :raises RuntimeError: if any of the argument validation fails
    :return: `argparse.Namespace` containing parsed args
    """
    parser = argparse.ArgumentParser(formatter_class=CustomHelpFormatter)
    parser.add_argument('sot_file',
                        metavar='source_of_truth_file.csv',
                        type=LazyFileType('r'),
                        help='file to read as source of truth')
    parser.add_argument('kustomize',
                        metavar='command_override',
                        nargs=argparse.REMAINDER,
                        help='kustomize build command override; collects all arguments after "--"')
    parser.add_argument('-b', '--base',
                        metavar='BASE_DIR',
                        type=pathlib.Path,
                        default=pathlib.Path('base_library/'),
                        help='path to base templates; default: base_library/')
    parser.add_argument('-o', '--overlay',
                        metavar='OVERLAY_DIR',
                        type=pathlib.Path,
                        default=pathlib.Path('overlays/'),
                        help='path to overlays; default: overlays/')
    parser.add_argument('-t', '--temp',
                        metavar='TEMP_DIR',
                        type=TempDir,
                        default=TempDir(),
                        help='path to temporary workdir; default: uses system temp')
    parser.add_argument('-y', '--hydrated',
                        metavar='HYDRATED_OUTPUT_DIR',
                        type=pathlib.Path,
                        default=pathlib.Path('output'),
                        help='path to render kustomize templates; default: $PWD/output')
    parser.add_argument('-s', '--output-subdir',
                        choices=('group', 'cluster', 'none'),
                        default='group',
                        help='type of output subdirectory to create; default: group')

    selector_mutex = parser.add_mutually_exclusive_group()
    selector_mutex.add_argument('--cluster-name',
                                metavar='CLUSTER_NAME',
                                help='name of cluster to select from config')
    selector_mutex.add_argument('--cluster-tag',
                                metavar='CLUSTER_TAG',
                                action='append',
                                help='tag to use to select clusters from config')
    selector_mutex.add_argument('--cluster-group',
                                metavar='CLUSTER_GROUP',
                                help='name of cluster group to select from config')

    verbosity_mutex = parser.add_mutually_exclusive_group()
    verbosity_mutex.add_argument('-v',
                                 '--verbose',
                                 action='count',
                                 help='increase output verbosity; -vv for max verbosity',
                                 default=0)
    verbosity_mutex.add_argument('-q',
                                 '--quiet',
                                 action='store_true',
                                 help='output errors only')

    args = parser.parse_args()

    # turn all provided paths into fully-resolved absolute paths
    args.base = args.base.resolve()
    args.overlay = args.overlay.resolve()
    args.hydrated = args.hydrated.resolve()

    # tags should be a set
    if args.cluster_tag:
        args.cluster_tag = {t for t in args.cluster_tag}

    for item in (args.base, args.overlay):
        try:
            assert item.exists(), (f'Provided base templates directory ({item}) does not exist')
            assert item.is_dir(), (
                f'Provided base templates directory ({item}) is not a directory')
            assert not item.is_file(), (
                f'Provided base templates directory ({item}) is not a directory')
        except AssertionError as e:
            logger.error(e)
            raise RuntimeError(e) from e

    return args


def main():
    try:
        args = parse_args()
    except RuntimeError:
        sys.exit(1)

    setup_logger(args)

    logger.debug(f'Received args: {pprint.pformat(vars(args))}')

    # get config from source-of-truth file
    with args.sot_file.open() as f:
        try:
            config_data = process_sot_file(f)
        except RuntimeError:
            sys.exit(1)

    args.temp()  # create tempdir
    logger.debug(f'Using temp dir: {args.temp.path}')

    jinja_env = setup_jinja(args.temp.path)  # setup jinja environment

    # a single cluster name is specified
    if args.cluster_name:
        cfg = config_data.get(args.cluster_name)
        logger.info(f'Processing cluster {args.cluster_name}')
        try:
            check_config(cfg)
            process_cluster(args, cfg, jinja_env, output_subdir=args.output_subdir)
        except (RuntimeError, RuntimeWarning):
            sys.exit(1)
    # filter by cluster tags, groups, or process all clusters in config
    else:
        for c, cfg in config_data.items():
            logger.info(f'Processing cluster {c}')
            try:
                check_config(cfg)
            except RuntimeError:
                continue  # if we encounter something "fatal" to the cluster, move onto the next

            # if tags provided as args
            if args.cluster_tag:
                try:
                    # split config tags into a set
                    config_tags = {t.strip() for t in cfg['cluster_tags'].split(",")}
                except KeyError:
                    logger.warning(f"Cluster '{c}': specified tags as args but has none in config "
                                   f"file")
                    continue

                # if config tags don't intersect with tags from args, then don't hydrate cluster
                if not config_tags.intersection(args.cluster_tag):
                    logger.debug(f"Cluster '{c}': no matching tags, not hydrating...")
                    continue
            if args.cluster_group:
                if args.cluster_group.strip().lower() != cfg['cluster_group'].strip().lower():
                    logger.debug(f"Cluster '{c}': not in group '{args.cluster_group}'; not "
                                 f"hydrating...")
                    continue

            try:
                process_cluster(args, cfg, jinja_env, output_subdir=args.output_subdir)
            except RuntimeError:
                logger.error('Nothing left to do; exiting without processing all clusters')
                sys.exit(1)
            except RuntimeWarning:
                continue
            finally:
                args.temp.cleanup()
                args.temp()
                logger.debug(f'Using temp dir: {args.temp.path}')
                jinja_env = setup_jinja(args.temp.path)

    args.temp.cleanup()


if __name__ == '__main__':
    main()
    logger.info('Exiting normally...')
