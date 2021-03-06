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

block_cipher = None

from PyInstaller import compat
from glob import glob
import platform

pathex=['src']
binaries=[]
hiddenimports=['numpy', 'scipy','PyQt4.uic','_struct']
outname='Eidolon'
datas=[
	('res','res'), 
	('config.ini','.'), 
	('tests','tests'), 
	('tutorial','tutorial'),
	('EidolonLibs/IRTK','EidolonLibs/IRTK'),
	('EidolonLibs/python','EidolonLibs/python'),
	('EidolonLibs/MIRTK/ffd_motion.cfg','EidolonLibs/MIRTK')
]

if compat.is_win:
	binaries=[
		('EidolonLibs/win64_mingw/bin/cg.dll','.'),
		('EidolonLibs/win64_mingw/bin/Plugin_CgProgramManager.dll','.'),
		('EidolonLibs/win64_mingw/bin/RenderSystem_GL.dll','.')
	]

	datas.append(('EidolonLibs/MIRTK/Win64','EidolonLibs/MIRTK/Win64'))
	
elif compat.is_darwin:
	outname+='.app'
	hiddenimports+=['appdirs','packaging','packaging.version','packaging.specifiers','packaging.requirements','packaging.utils','cProfile']	
	datas+=[
		('EidolonLibs/osx/bin/Ogre.framework','Contents/Frameworks/Ogre.framework'),
		('EidolonLibs/osx/bin/OgreOverlay.framework','Contents/Frameworks/OgreOverlay.framework'),
		('EidolonLibs/osx/bin/Cg.framework','Contents/Frameworks/Cg.framework'),
		('EidolonLibs/osx/bin/Plugin_CgProgramManager.framework','Contents/Frameworks/Plugin_CgProgramManager.framework'),
		('EidolonLibs/osx/bin/RenderSystem_GL.framework','Contents/Frameworks/RenderSystem_GL.framework'),
		('EidolonLibs/MIRTK/OSX','EidolonLibs/MIRTK/OSX')
	]
elif compat.is_linux:
	hiddenimports+=['scipy._lib.messagestream']
	binaries+=[(f,'.') for f in glob('EidolonLibs/linux/bin/*')]
	datas.append(('EidolonLibs/MIRTK/Linux','EidolonLibs/MIRTK/Linux'))

a = Analysis(['main.py'],
             pathex=pathex,
             binaries=binaries,
             datas=datas,
             hiddenimports=hiddenimports,
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='Eidolon',
          debug=False,
          strip=False,
          upx=True,
          console=True )
coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=False,
               upx=True,
               name=outname)
