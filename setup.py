#!/usr/bin/python

import os
#from distutils.core import setup
from setuptools import setup

root_dir = os.environ["PWD"]

setup(name = "resmon",
      version = "0.1",
      description = "Resource monitor",
      author = "Allen Hung",
      author_email = "allenhung8@gmail.com",
      packages = ["resmon"],
      package_data={"resmon": [ os.path.join(root_dir, "resmon-functions") ]},
      scripts = ["resmond", "resmon-cli"],
      install_requires = ["psutil"],
      data_files=[("/usr/lib/resmon", ["resmon-functions"])]
     )
