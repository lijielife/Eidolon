# Eidolon Biomedical Framework
# Copyright (C) 2016-8 Eric Kerfoot, King's College London, all rights reserved
# 
# This file is part of Eidolon.
#
# Eidolon is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# Eidolon is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License along
# with this program (LICENSE.txt).  If not, see <http://www.gnu.org/licenses/>
'''
This module imports the PyQt modules in a version-compatible manner, defines a QtVersion variable set to 4 or 5, and loads
the UI class definitions from the .ui files or from resource modules containing them. By loading .ui files directly the
application doesn't have to rely on source files generated by pyuic4/5 which are specific to their version of PyQt, thus
with the uniform importing allows cross-version compatibility. This will also try to import the resource module meant
for the loaded PyQt version. 

If the .ui files are not present then there should be a resource file containing them as resource items, and this is
loaded instead. This is the case when packaged as an application using pyinstaller which will include the resource file
but not .ui files. These resource files are version dependent and generated by setup.py.

The defined modules are QtGui, QtCore, Qt, QtWidgets (which is QtGui for PyQt4), and uic. These should be loaded by 
modules requiring PyQt types rather than from PyQt4/5 directly. Modules requiring PyQt should use the following import:
    
    from ui import Qt, QtCore, QtGui, QtWidgets, QtVersion
'''


import sys
import os
import glob
import re
import contextlib

# attempt to import PyQt5 first then default to PyQt4
try: 
    from PyQt5 import QtGui, QtCore, QtWidgets, uic
    from PyQt5.QtCore import Qt
    from . import Resources_rc5
    QtVersion=5
except ImportError:
    from PyQt4 import QtCore, QtGui, uic
    from PyQt4.QtCore import Qt
    from . import Resources_rc4
    QtWidgets=QtGui
    QtVersion=4

# Python 2 and 3 support
try: 
    from StringIO import StringIO
except ImportError:
    from io import StringIO
    

module=sys.modules[__name__] # this module
restag=re.compile('<resources>.*</resources>',flags=re.DOTALL) # matches the resource tags in the ui files


def loadUI(xmlstr):
    '''Load the given XML ui file data and store the created type as a member of this module.'''
    s=str(xmlstr)
    s=re.sub(restag,'',s) # get rid of the resources section in the XML
    uiclass,_=uic.loadUiType(StringIO(s)) # create a local type definition
    setattr(module,uiclass.__name__,uiclass) # store as module member
    

# list all .ui files, if there are none then attempt to load from a resource script file
uifiles=glob.glob(os.path.join(os.path.dirname(__file__),'*.ui'))
if len(uifiles)!=0:
    # load the class from each ui file and store it as a member of this module
    for ui in uifiles:
        loadUI(open(ui).read())
else:
    # load the resource module containing the .ui files appropriate to which version of PyQt is being used
    if QtVersion==5:
        from . import UI_rc5
    else:
        from . import UI_rc4
        
    # iterate over every file in the layout section of the resources and load them into this module
    it=QtCore.QDirIterator(':/layout')
    while it.hasNext():
        with contextlib.closing(QtCore.QFile(it.next())) as layout:
            if layout.open(QtCore.QFile.ReadOnly):
                loadUI(layout.readAll())
                