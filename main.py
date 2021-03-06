import argparse
import hashlib
import os
import re
import sys
from adb_android import adb_android
from pysqlcipher import dbapi2 as sqlite
from xml.etree import ElementTree


class HostException(Exception):
    pass


class DeviceException(Exception):
    pass


# Thanks to Fabric for this!
# https://github.com/fabric/fabric
def _wrap_with(code):
    def inner(text):
        return "\033[1;%sm%s\033[0m" % (code, text)
    return inner

red = _wrap_with('31')
green = _wrap_with('32')
yellow = _wrap_with('33')
blue = _wrap_with('34')
magenta = _wrap_with('35')
cyan = _wrap_with('36')
white = _wrap_with('37')


def _shell_command(command):
    host_status_code, output = adb_android.shell('%s; echo $?' % command)
    if host_status_code != 0:
        raise HostException
    output = output.splitlines()
    device_status_code = int(output[-1])
    if device_status_code != 0:
        raise DeviceException
    return output[:-1]


def _su_shell_command(command):
    return _shell_command('su -c "%s"' % command)


wechat_user_directories_regex = re.compile('^[0-9A-Fa-f]{32}$')


def _find_candidate_dbs():
    output = _su_shell_command('ls /data/data/com.tencent.mm/MicroMsg/')
    candidates = filter(lambda x: wechat_user_directories_regex.match(x) != None, output)
    found_dbs = []
    for candidate in candidates:
        full_db_path = '/data/data/com.tencent.mm/MicroMsg/%s/EnMicroMsg.db' % candidate
        try:
            _su_shell_command('ls %s' % full_db_path)
        except DeviceException:
            continue
        found_dbs.append(full_db_path)
    return found_dbs


def find_dbs(args):
    found_dbs = _find_candidate_dbs()
    if len(found_dbs) == 0:
        print red('=' * 80)
        print red('Could not find any candidate databases.')
        sys.exit(1)
    print green('=' * 80)
    print green('Found the following database(s):')
    print
    for db in found_dbs:
        print cyan('  %s' % db)
    print
    if len(found_dbs) > 1:
        print white('Run `%s pull --database-path <PATH_TO_DB> <OUTPUT_FILE>` to import one.' % sys.argv[0])
    else:
        print white('Run `%s pull <OUTPUT_FILE>` to import it.' % sys.argv[0])


def _find_nonroot_writable_dir():
    dirs_to_try = ['/sdcard']
    for candidate_dir in dirs_to_try:
        try:
            _shell_command('touch %s/foo && rm %s/foo' % (candidate_dir, candidate_dir))
        except DeviceException:
            continue
        else:
            return candidate_dir
    raise Exception


def pull_db(args):
    db_to_pull = args.database_path
    if not db_to_pull:
        found_dbs = _find_candidate_dbs()
        if len(found_dbs) == 0:
            print red('=' * 80)
            print red('Could not find any candidate databases.')
            sys.exit(1)
        if len(found_dbs) > 1:
            print red('=' * 80)
            print red('More than one candidate database found!')
            print
            print white('Run `%s find` to see the options, and then come back with a specific path for me.' % sys.argv[0])
            sys.exit(1)
        db_to_pull = found_dbs[0]

    # Find a writable location
    temp_dir = _find_nonroot_writable_dir()

    try:
        # Copy the database there first
        print yellow('Copying the DB to a user directory...')
        _su_shell_command('cp %s %s/EnMicroMsg.db' % (db_to_pull, temp_dir))

        print yellow('Pulling the DB to your computer...')
        status_code, output = adb_android.pull('%s/EnMicroMsg.db' % temp_dir, args.output_file)
    except:
        print
        print red('=' * 80)
        print red('An error occurred.')
        sys.exit(1)
    else:
        print
        print green('=' * 80)
        print green('Success!')
    finally:
        # Make sure to clean up!
        print yellow('Cleaning up...')
        _shell_command('rm -f %s/EnMicroMsg.db' % temp_dir)


def get_default_uin(args):
    shared_prefs_xml = ''.join(_su_shell_command('cat /data/data/com.tencent.mm/shared_prefs/system_config_prefs.xml'))
    xml_tree = ElementTree.fromstring(shared_prefs_xml)
    candidates = filter(lambda x: x.attrib['name'] == 'default_uin', xml_tree.getchildren())
    if len(candidates) != 1:
        print red('=' * 80)
        print red('Could not parse system_config_prefs.xml.')
        sys.exit(1)
    default_uin = candidates[0].attrib['value']
    print green('=' * 80)
    print green('Found the following UIN:')
    print
    print cyan('  %s' % default_uin)


def _delete_file_if_exists(file_path):
    try:
        os.remove(file_path)
    except OSError:
        pass


def _generate_key(imei, uin):
    key = hashlib.md5(str(imei) + str(uin)).hexdigest()[0:7]
    return key


def decrypt(args):
    print
    print yellow('Generating key...')
    key = _generate_key(args.imei, args.uin)
    print
    print green('=' * 80)
    print green('The key is:')
    print
    print cyan('  %s' % key)
    print
    print yellow('Decrypting, hang on...')
    _delete_file_if_exists(args.output_file)
    conn = sqlite.connect(args.input_file)
    c = conn.cursor()
    try:
        c.execute('PRAGMA cipher_default_use_hmac = OFF;')
        c.execute('PRAGMA key = \'%s\';' % key)
        c.execute('ATTACH DATABASE \'%s\' AS wechatdecrypted KEY \'\';' % args.output_file)
        c.execute('SELECT sqlcipher_export(\'wechatdecrypted\');')
        c.execute('DETACH DATABASE wechatdecrypted;')
    except:
        print
        print red('=' * 80)
        print red('An error occurred.')
        sys.exit(1)
    else:
        print
        print green('=' * 80)
        print green('Success!')
    finally:
        c.close()


def encrypt(args):
    print
    print yellow('Generating key...')
    key = _generate_key(args.imei, args.uin)
    print
    print green('=' * 80)
    print green('The key is:')
    print
    print cyan('  %s' % key)
    print
    print yellow('Encrypting, hang on...')
    _delete_file_if_exists(args.output_file)
    conn = sqlite.connect(args.input_file)
    c = conn.cursor()
    try:
        c.execute('PRAGMA cipher_default_use_hmac = OFF;')
        c.execute('ATTACH DATABASE \'%s\' AS encrypted KEY \'%s\';' % (args.output_file, key))
        c.execute('PRAGMA cipher_use_hmac = OFF;')
        c.execute('SELECT sqlcipher_export(\'encrypted\');')
        c.execute('DETACH DATABASE encrypted;')
    except:
        print
        print red('=' * 80)
        print red('An error occurred.')
        sys.exit(1)
    else:
        print
        print green('=' * 80)
        print green('Success!')
    finally:
        c.close()


def push_db(args):
    # Find a writable location
    temp_dir = _find_nonroot_writable_dir()

    try:
        # Copy the DB to the phone first
        print yellow('Pushing the DB to the device...')
        status_code, output = adb_android.push(args.input_file, '%s/EnMicroMsg.db' % temp_dir)

        print yellow('Copying the DB to its final home...')
        _su_shell_command('cp %s/EnMicroMsg.db %s' % (temp_dir, args.database_path))
    except:
        print
        print red('=' * 80)
        print red('An error occurred.')
        sys.exit(1)
    else:
        print
        print green('=' * 80)
        print green('Success!')
    finally:
        # Make sure to clean up!
        print yellow('Cleaning up...')
        _shell_command('rm -f %s/EnMicroMsg.db' % temp_dir)


def parse_args():
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers()

    find_args = subparsers.add_parser('find')
    find_args.set_defaults(fn=find_dbs)

    pull_args = subparsers.add_parser('pull')
    pull_args.set_defaults(fn=pull_db)
    pull_args.add_argument('output_file',
                           metavar='OUTPUT_FILE',
                           type=str)
    pull_args.add_argument('--database-path',
                           type=str,
                           help='Absolute path to the database to pull from the phone.'
                                'If not specified, and only one database is found, that'
                                'database will be auto-selected.')

    get_uin_args = subparsers.add_parser('get_uin')
    get_uin_args.set_defaults(fn=get_default_uin)

    decrypt_args = subparsers.add_parser('decrypt')
    decrypt_args.set_defaults(fn=decrypt)
    decrypt_args.add_argument('uin',
                              metavar='UIN',
                              type=str)
    decrypt_args.add_argument('imei',
                              metavar='IMEI',
                              type=str)
    decrypt_args.add_argument('input_file',
                              metavar='INPUT_FILE',
                              type=str)
    decrypt_args.add_argument('output_file',
                              metavar='OUTPUT_FILE',
                              type=str)

    encrypt_args = subparsers.add_parser('encrypt')
    encrypt_args.set_defaults(fn=encrypt)
    encrypt_args.add_argument('uin',
                              metavar='UIN',
                              type=str)
    encrypt_args.add_argument('imei',
                              metavar='IMEI',
                              type=str)
    encrypt_args.add_argument('input_file',
                              metavar='INPUT_FILE',
                              type=str)
    encrypt_args.add_argument('output_file',
                              metavar='OUTPUT_FILE',
                              type=str)

    push_args = subparsers.add_parser('push')
    push_args.set_defaults(fn=push_db)
    push_args.add_argument('input_file',
                           metavar='INPUT_FILE',
                           type=str)
    push_args.add_argument('database_path',
                           metavar='DATABASE_PATH',
                           type=str)

    return parser.parse_args()


def main():
    args = parse_args()

    print
    print yellow('Waiting for device...')

    adb_android.wait_for_device()

    args.fn(args)


if __name__ == '__main__':
    main()
