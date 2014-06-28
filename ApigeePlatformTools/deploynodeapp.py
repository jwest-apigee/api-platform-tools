import argparse
import getpass
import json
import re
import os
import os.path
import sys
import StringIO
import zipfile

from ApigeePlatformTools import httptools, deploytools


def configure_argparse():
  """
  Configures an argparse parser for parsing the command line arguments

  :return:
  """
  parser = argparse.ArgumentParser(
    description='This command allows you to deploy a Node.js application to Apigee Edge.  It will zip up '
                'the node_modules directory and include it in a zip file that is uploaded to Apigee and '
                'deployed according to the parameters issued',
    epilog='NOTE: The "default" Virtual Host listens on HTTP.\nFor an HTTPS-only app, use "-x secure".')

  parser.add_argument('-c', '--config',
    help='Path to a configuration file that overrides all command-line parameters with the exception of -p',
    metavar='config file path',
    default=None)

  parser.add_argument('-o', '--organization',
    help='Apigee organization name',
    metavar='org name',
    default=None)

  parser.add_argument('-e', '--environment',
    help='Apigee environment name',
    metavar='env name')

  parser.add_argument('-d', '--directory',
    help='The Directory where the Node application is stored',
    metavar='dir',
    default=None)

  parser.add_argument('-n', '--name',
    help='The Apigee proxy name',
    metavar='proxy name',
    default=None)

  parser.add_argument('-m', '--main_script',
    help='Main script name: Should be at the top level of the directory',
    metavar='script name',
    default=None)

  parser.add_argument('-u', '--username',
    help='Apigee user name',
    metavar='username',
    default=None)

  parser.add_argument('-p', '--password',
    help='Apigee password',
    metavar='password',
    default=None)

  parser.add_argument('-b', '--base_path',
    help='Base path for the deployed proxy',
    metavar='base path',
    default='/')

  parser.add_argument('-l', '--apigee_url',
    help='Apigee Management API URL',
    metavar='url',
    default='https://api.enterprise.apigee.com')

  parser.add_argument('-z', '--zip_file',
    help='ZIP file to save (optional for debugging)',
    metavar='zip file',
    default=None)

  parser.add_argument('-x', '--virtual_host',
    help='Virtual Host name',
    metavar='virtual host',
    choices=['default', 'secure'],
    default='default')

  parser.add_argument('-i', '--import_only',
    help='import only, do not deploy',
    action='store_true',
    default=None)

  return parser


def run():
  parser = configure_argparse()

  args = vars(parser.parse_args(sys.argv[2:]))

  # this will merge the values in the config file if specified
  # with the ones from argparse
  if args.get('config') is not None:
    config_file = args.get('config')

    # check if file exists
    if not os.path.isfile(config_file):
      print 'The specified Config File (%s) cannot be found' % config_file
      sys.exit(1)

    try:
      # open and parse file
      with open(config_file, 'r') as f:
        print 'Configuration File Specified: %s ' % config_file
        config = json.load(f)

        print json.dumps(config, indent=2)
        print ''

    except ValueError, e:
      print 'The specified Config File (%s) is not valid JSON!' % config_file
      sys.exit(1)

    # argparse returns strings in ASCII whereas json.load() creates a dict with unicode
    # if you have some ascii and some unicode the zip part of the process fails
    for key, value in config.iteritems():
      ekey = key.encode('ascii')

      if type(value) == unicode:
        evalue = value.encode('ascii')
      else:
        evalue = value

      args[ekey] = evalue

  apigee_url = args.get('apigee_url')
  username = args.get('username')
  password = args.get('password')
  directory = args.get('directory')
  main_script = args.get('main_script')
  organization = args.get('organization')
  environment = args.get('environment')
  name = args.get('name')
  base_path = args.get('base_path')
  should_deploy = args.get('should_deploy')
  zip_file = args.get('zip_file')
  virtual_host = args.get('virtual_host')

  while username is None or username == '':
    try:
      print 'Enter username (Ctl-C to exit):'
      username = sys.stdin.readline()[:-1]
      pass
    except KeyboardInterrupt:
      print '\n Username is required! Exiting...'
      sys.exit(1)

  while password is None or password == '':
    try:
      password = getpass.getpass('Password (Ctl-C to exit):')

    except KeyboardInterrupt:
      print '\n Password is required! Exiting...'
      sys.exit(1)

  bad_usage = False
  if directory is None:
    bad_usage = True
    print '-d is required'
  if environment is None:
    bad_usage = True
    print '-e is required'
  if name is None:
    bad_usage = True
    print '-n is required'
  if organization is None:
    bad_usage = True
    print '-o is required'
  if main_script is None:
    bad_usage = True
    print '-m is required'

  if bad_usage:
    parser.print_help()
    sys.exit(1)

  httptools.setup(apigee_url, username, password)

  def make_app_xml(policies=None):
    if not policies or len(policies) == 0:
      app_xml = '<APIProxy name="%s"/>' % name

    else:
      # will add policies later...
      app_xml = '<APIProxy name="%s">' % name
      app_xml += '</APIProxy>'

    return app_xml

  def make_proxy_xml():
    return """
            <ProxyEndpoint name = "default">
                <HTTPProxyConnection>
                    <BasePath>%s</BasePath>
                    <VirtualHost>%s</VirtualHost>
                </HTTPProxyConnection>
                <RouteRule name="default">
                    <TargetEndpoint>default</TargetEndpoint>
                </RouteRule>
            </ProxyEndpoint> """ % (base_path, virtual_host)

  def make_target_xml():
    return """
        <TargetEndpoint name="default">
            <ScriptTarget>
                <ResourceURL>node://%s</ResourceURL>
            </ScriptTarget>
        </TargetEndpoint> """ % main_script

  def path_contains_dot(p):
    """
    Return TRUE if any component of the file path contains a directory name that
    starts with a "." like '.svn', but not '.' or '..'

    :param p: The path to check
    :return: True if any component of the path has a '.' in it, False otherwise
    """
    c = re.compile('\.\w+')
    for pc in p.split('/'):
      if c.match(pc) != None:
        return True
    return False

  def zip_directory(dir, pfx):
    """
    ZIP a whole directory into a stream and return the result so that it can be nested into the top-level ZIP
    :param dir:
    :param pfx:
    :return:
    """
    print 'Zip: Zipping Directory: %s' % dir
    ret = StringIO.StringIO()
    zip_file = zipfile.ZipFile(ret, 'w')
    dir_list = os.walk(dir)

    for dirEntry in dir_list:

      for fileEntry in dirEntry[2]:

        if not fileEntry.endswith('~'):

          fn = os.path.join(dirEntry[0], fileEntry)
          en = os.path.join(pfx, os.path.relpath(dirEntry[0], dir), fileEntry)

          if os.path.isfile(fn):
            zip_file.write(fn, en)

    zip_file.close()
    return ret.getvalue()

  # Construct a Zipped copy of the bundle in memory
  tf = StringIO.StringIO()
  zipout = zipfile.ZipFile(tf, 'w')

  zipout.writestr('apiproxy/%s.xml' % name, make_app_xml())
  zipout.writestr('apiproxy/proxies/default.xml', make_proxy_xml())
  zipout.writestr('apiproxy/targets/default.xml', make_target_xml())

  for topName in os.listdir(directory):

    if topName == zip_file:
      print 'Zip: Skipping file %s which is the target zip dir for the proxy!' % zip_file
      continue

    if not path_contains_dot(topName):
      fn = os.path.join(directory, topName)

      if os.path.isdir(fn):
        contents = zip_directory(fn, topName)
        en = 'apiproxy/resources/node/%s.zip' % topName
        print 'Zip: Adding zipped directory %s' % en
        zipout.writestr(en, contents)
      else:
        en = 'apiproxy/resources/node/%s' % topName
        print 'Zip: Adding file %s' % en
        zipout.write(fn, en)

  zipout.close()

  if zip_file is not None:

    if os.path.isfile(zip_file):
      print 'Zip: Overwriting the specified ZIP file: %s' % zip_file
      os.remove(zip_file)

    tzf = open(zip_file, 'wb')
    tzf.write(tf.getvalue())
    tzf.close()

  revision = deploytools.importBundle(organization, name, tf.getvalue())

  if revision < 0:
    sys.exit(2)

  print 'Imported new app revision %i' % revision

  if should_deploy:
    status = deploytools.deployWithoutConflict(organization, environment, name, '/', revision)

    if not status:
      sys.exit(2)

  response = httptools.httpCall('GET',
    '/v1/o/%s/apis/%s/deployments' % (organization, name))
  deps = deploytools.parseAppDeployments(organization, response, name)
  deploytools.printDeployments(deps)


if __name__ == '__main__':
  run()