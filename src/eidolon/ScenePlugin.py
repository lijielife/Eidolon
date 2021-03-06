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


import os
import glob
import numpy as np

from . import Utils
from . import MeshAlgorithms
from . import ImageAlgorithms
from . import VisualizerUI
from . import SceneComponents
from . import SceneUtils

from renderer import TF_ALPHALUM8, TF_RGBA32, vec3, rotator,color, minmaxMatrixReal
from .Utils import first, ParamType, ParamDef, Future, taskroutine, timing, toIterable
from .MathDef import ElemType
from .VisualizerUI import CustomUIType, setChecked, fillList, ParamPanel, IconName
from .MeshAlgorithms import ValueFunc, UnitFunc, VecFunc, calculateFieldMinMax
from .SceneObject import ReprType,SceneObjectRepr, MeshSceneObject, MeshSceneObjectRepr, TDMeshSceneObjectRepr
from .ImageObject import ImageSceneObject, ImageSceneObjectRepr,ImageSeriesRepr, ImageVolumeRepr


ctImageRange=(ImageAlgorithms.Hounsfield.min,ImageAlgorithms.Hounsfield.max)


class PluginError(Exception):
    '''Exception used to indicate plugin-specific error conditions.'''
    def __init__(self,plugin,msg):
        self.plugin=plugin
        msg='Error in plugin %r: %s'%(plugin.name,msg)
        Exception.__init__(self,msg)


def getReprTypeFromStrBox(strbox):
    '''
    Returns the ReprType name for the representation description found in `strbox', which is either a str object or an
    object with a currentText() method which returns one (ie. a QComboBox).
    '''
    tstr=strbox if isinstance(strbox,str) else str(strbox.currentText())
    return first(i[0] for i in ReprType if i[1]==tstr)


def delegatedmethod(meth):
    '''
    Decorator for marking plugin methods as callable from scene objects or representations associated with that plugin.
    The method should accept an scene object/representation as its first argument after self. When the method is called
    with the scene object/representation as the receiver, a proxy callable is returned which places the receiver as the
    first argument followed by whatever other arguments are given. This allows calls of the form obj.plugin.foo(obj,...)
    to be replaced with obj.foo(...). Decorated methods have a new member '__isdelegatedmethod__' containing True. An
    overridden method will still be treated as a delegated method even if it does not have this decorator applied.
    '''
    setattr(meth,'__isdelegatedmethod__',True)
    return meth


class ScenePlugin(object):
    '''
    Base class for all plugins, includes code for a basic properties box for scene objects and representations.

    The file interface is defined by these methods, overriding any one is optional unless specified otherwise:
        - acceptFile - returns True if the given file can be loaded with loadObject (which must also be overridden)
        - loadObject - load a file(s)
        - saveObject - save an object to file(s)
        - checkFileOverwrite - check whether saving an object would overwrite files
        - copyObjFiles - copies an object's file(s)
        - renameObjFiles - renames an object's file(s)
        - getObjFiles - get the files the given object was loaded from
    '''
    
    def __init__(self,name):
        self.plugid=-1 # plugin ID number
        self.win=None # main window, may be None if no UI is created
        self.mgr=None # scene manager
        self.name=name # plugin name, must be unique amongst loaded plugins
        
    def init(self,plugid,win,mgr):
        '''Called when the manager is being initialized.'''
        self.plugid=plugid
        self.win=win
        self.mgr=mgr
        
    def cleanup(self):
        '''Called when shutting down, use this to clear and close resources.'''
        pass

    def findObject(self,name,assertFound=True):
        '''
        Attempt to find the file specified by `name', which is a str or a Future containing one, otherwise it's returned
        instead of a found object. If `name' is an existing filename, the basename without extension is used as the
        name. If an object with the given name can't be found and `assertFound' is True, then an assertion error is
        raised. The found object is returned or None if not found.
        '''
        name=Future.get(name)
        if not isinstance(name,str):
            return name

        if os.path.isfile(name):
            name=Utils.splitPathExt(name)[1]

        obj=self.mgr.findObject(name)
        assert not assertFound or obj!=None,"Can't find %r"%name
        return obj

    def removeFilesTask(self,fileglob):
        '''Deletes the files matching the regular expression string `fileglob' in a task.'''
        def _remove():
            for f in glob.glob(fileglob):
                os.remove(f)
                
        self.mgr.addFuncTask(_remove,'Removing Files')

    @delegatedmethod
    def getScriptCode(self,obj,**kwargs):
        '''
        Return the Python code string to create, load, or initialize the given object. This is called by ScriptWriter
        to write plugin-specific loading code to a script. The object `obj' is expected to be a SceneObject or
        SceneObjectRepr instance whose plugin is the receiver object (ie. obj.plugin==self).

        The `kwargs' value will contain important information about what code to generate:
         - "namemap" is keyed to the object->variable  dictionary used to determine what variable name refers to `obj'
           and other objects such as materials. This must be used to get variable names for all objects, simply
           declaring a new variable can lead to conflicts in other parts of the code.
         - "configSection" is True if code to configure an object is to be generated rather than creation code
         - "setMaterial" is True if the material for a rep object should be set in configuration code.
         - "scriptdir" may be present in `kwargs', if so then a script file is being generated and all file paths
           should be specified relative to the variable of the same name. A convenience function keyed to "convertPath"
           may be present in `kwargs' which accepts a path and returns code stating the path relative to "scriptdir".

        Code should not assume these values are present always, use "kwargs.get()" with an appropriate default value.
        The default implementation of this method provides the config code for a SceneObjectRepr object, other
        functionality must be provided by overrides.
        '''
        configSection=kwargs.get('configSection',False)
        setMaterial=kwargs.get('setMaterial',True)
        namemap=kwargs.get('namemap',{})
        varname=namemap[obj]
        pname="None"

        if configSection and isinstance(obj,SceneObjectRepr):
            parent=obj.getParent()
            pname=namemap.get(parent,"None")
            args={
                'varname':varname,
                'trans':str(obj.getTransform()),
                'pname':str(pname),
                'bb':str(self.mgr.isBoundBoxShown(obj)),
                'handle':str(self.mgr.isHandleShown(obj))
            }

            script='''
                mgr.showBoundBox(%(varname)s,%(bb)s)
                mgr.showHandle(%(varname)s,%(handle)s)
                mgr.setReprProps(%(varname)s,trans=%(trans)s,parent=%(pname)s)
            '''

            matname=obj.getMaterialName()
            if setMaterial and matname not in (None,'','Default'):
                script+='%(varname)s.applyMaterial(%(matname)s)\n'
                args['matname']=namemap[matname]

            return Utils.setStrIndent(script % args).strip()+'\n'

        return ''

    def getIcon(self,obj):
        '''Returns the icon name for `obj' (which will likely be a member of IconName), or None for the default.'''
        return None

    def getMenu(self,obj):
        '''
        Returns the menu structure for `obj' and the callback for when an item is clicked, typically self.objectMenuItem.
        The menu structure is a list whose first element is the menu title, followed by values satisfying createMenu().
        The callback take two arguments, the object associated with the menu and the text of the menu item clicked.
        '''
        return None, self.objectMenuItem

    def getHelp(self):
        '''Return a help text block.'''
        return ''
    
    def getTests(self):
        '''Return a list of test tuples of the form (callable, arg1, arg2,...) to be called with Nose.'''
        return []

    def acceptFile(self,filename):
        '''
        Return True if `filename' is a file which can be loaded by this plugin, and if other preconditions are met.
        When a file is given on the command line which isn't a script file, the file path is pass to this method with
        every loaded plugin as a receiver. The first plugin to return True then loads the file through loadObject().
        '''
        return False

    def loadObject(self,filename,name=None,**kwargs):
        '''
        Load SceneObject(s) from the file `filename' and gives the object the name `name' or one based off the filename
        if this is None. Additional arguments can be given in later overrides of this method. The filename can be a
        prefix for a number of filenames or one of multiple files needed to load the object, this isn't necessarily
        the only file read. An IOError should be thrown if any loading cannot be completed correctly, other exceptions
        if the data is incorrect. A SceneObject is returned or an iterable thereof if multiple objects can be loaded.
        The return value varies by plugin and can be a Future object.
        '''
        raise NotImplementedError('Cannot load files as SceneObjects')

    @delegatedmethod
    def saveObject(self,obj,path,overwrite=False,setFilenames=False,**kwargs):
        '''
        Save a SceneObject `obj' to the file or directory `path'. The plugin for `obj' need not necessarily be `self' if
        a plugin wants to handle saving arbitrary objects of the right type. If the plugin must be `self' then raise a
        ValueError if it's not. Files are written into the directory component of `path' which must exist, the name of
        the files will be derived from that of `obj' if `path' is a directory and not a full filename. An IOError is
        raised if files would be overwritten and `overwrite' is False, in which case no files must be created or altered.
        The internal representation of `obj' should be changed to record what the saved filenames are if `setFilenames'
        is True, it's up to the plugin to determine how. The return value varies by plugin and can be a Future object.
        '''
        raise NotImplementedError('Cannot save files for this object')

    @delegatedmethod
    def checkFileOverwrite(self,obj,dirpath,name=None):
        '''
        Returns the list of file paths which would be overwritten if `obj' was saved to the directory `dirpath' with
        its current name replaced with `name' if not None. An empty list means saving this object or moving files to
        use the name `name' would not overwrite any existing files. This must function even if the `obj' was not loaded
        from files.
        '''
        raise NotImplementedError('Cannot determine file overwrites')

    @delegatedmethod
    def getObjFiles(self,obj):
        '''
        Get the paths to the files `obj' was loaded from. Returns [] if `obj' wasn't loaded from files but can be
        saved to files, or the default None if it can't be saved to a file (ie. a transitive object with no data).
        '''
        return None

    @delegatedmethod
    def renameObjFiles(self,obj,oldname,overwrite=False):
        '''
        Rename files for object `obj' so that their names match the current name, previous name being `oldname'. This
        will overwrite files only if `overwrite' is True, if not then an IOError is raised and nothing is done if files
        would have to be overwritten. This requires that `obj' was previously loaded from or saved to files.
        '''
        raise NotImplementedError('Cannot move files when renaming objects')

    @delegatedmethod
    def copyObjFiles(self,obj,sdir,overwrite=False):
        '''
        Copy the object `obj' to directory `sdir' and set its internal representation to match the filename(s). This
        will raise an IOError and do nothing if files need to be overwritten and `overwrite' is False. This requires
        that `obj' was previously loaded from or saved to files.
        '''
        raise NotImplementedError('Cannot save files for this object')

    def removeObject(self,obj):
        '''This should be called if another plugin takes responsibility for `obj' away from the current one.'''
        assert obj.plugin==self
        pass

    @delegatedmethod
    def createRepr(self,obj,reprtype,**kwargs):
        '''
        Returns a representation of 'obj' SceneObject instance as defined by the 'reprtype' type and
        other arguments. This is typically called by the method of 'obj' of the same name rather than directly.
        '''
        pass

    @delegatedmethod
    def getReprParams(self,obj,reprtype):
        '''Returns the list of ParamDef objects defining the parameters for the given given representation type.'''
        return []

    @delegatedmethod
    def getReprParamHelp(self,obj,reprtype):
        '''Get the help text for the parameters for `obj' and representation type `reprtype'.'''
        params=self.getReprParams(obj,reprtype)
        s='Representation type "%s":\n   ' %reprtype
        return s+'\n   '.join(map(str,params))

    @delegatedmethod
    def getReprTypes(self,obj):
        '''Return the ReprType identifiers for the valid representations of this object.'''
        return []

    @delegatedmethod
    def createHandles(self,rep,**kwargs):
        '''
        Create a list of Handle objects for representation `rep'. By default this creates a single TransFormHandle
        object on every call. This method must safely return the handle list regardless of how many times its called.
        '''
        return [SceneComponents.TransformHandle(rep)]

    def updateObjPropBox(self,obj,prop):
        '''Updates the properties dialog 'prop' for SceneObject 'obj'. Usually only called by the UI when refreshing.'''
        if not prop.isVisible():
            return

        if prop.propTable.rowCount()==0:
            VisualizerUI.fillTable(obj.getPropTuples(),prop.propTable)

        reprs=[ReprType[r][0] for r in obj.getReprTypes()]
        fillList(prop.reprsBox,reprs,prop.reprsBox.currentIndex())
        if len(reprs)==0:
            prop.reprsBox.setVisible(False)
            prop.reprTypeLabel.setVisible(False)

    def updateReprPropBox(self,rep,prop):
        '''Updates the properties dialog 'prop' for SceneObjectRepr 'Rep'. Usually only called by the UI when refreshing.'''
        if not prop.isVisible():
            return

        if prop.propTable.rowCount()==0:
            VisualizerUI.fillTable(rep.getPropTuples(),prop.propTable)

        fillList(prop.matnameBox,self.mgr.listMaterialNames(),rep.matname)
        setChecked(rep.isVisible(),prop.visibleCheckbox)
        setChecked(self.mgr.isBoundBoxShown(rep),prop.bbCheckbox)
        setChecked(self.mgr.isHandleShown(rep),prop.handleCheckbox)

        pname=rep.rparent.name if rep.getParent() else '<None>'
        fillList(prop.parentBox,[r.name for r in self.mgr.enumSceneObjectReprs() if r!=rep],pname,'None')

        pos=rep.getPosition()
        scale=rep.getScale()
        prop.setPosition(pos.x(),pos.y(),pos.z())
        prop.setScale(scale.x(),scale.y(),scale.z())
        prop.setRotation(*rep.getRotation())

        panel=prop.paramPanel
        if panel:
            for param in panel.params:
                panel.setParam(param.name,rep.getParam(param.name))

    def createObjPropBox(self,obj):
        '''Creates a properties dialog box for SceneObject 'obj'. This should be a new instance of a QWidget subclass.'''
        prop=VisualizerUI.ObjectPropertyWidget()
        prop.createButton.clicked.connect(lambda:self._createReprButton(obj,prop))
        return prop

    @delegatedmethod
    def createReprPropBox(self,rep):
        '''Creates a properties dialog box for SceneObjectRepr 'rep'. This should be a new instance of a QWidget subclass.'''
        prop=VisualizerUI.ObjectReprPropertyWidget()

        prop.spectrumBox.setVisible(False) # don't use spectrum box by default

        params=rep.getParamDefs()
        if len(params)>0:
            def _setParam(name,value):
                rep.setParam(name,value)
                self.mgr.repaint()

            panel=ParamPanel(params)
            prop.setParamPanel(panel)
            panel.setParamChangeFunc(_setParam)

        prop.visibleCheckbox.stateChanged.connect(lambda:self._setReprVisibleCheckbox(rep))
        prop.handleCheckbox.stateChanged.connect(lambda:self.mgr.showHandle(rep,prop.handleCheckbox.isChecked()))
        prop.bbCheckbox.stateChanged.connect(lambda:self.mgr.showBoundBox(rep,prop.bbCheckbox.isChecked()))
        prop.applymatButton.clicked.connect(lambda:self._applyMaterialButton(rep,prop))
        prop.parentBox.currentIndexChanged.connect(lambda i:self._setParent(rep,prop))

        def setPosition():
            rep.setPosition(vec3(*prop.getPosition()))
            self.mgr.repaint()

        def setScale():
            rep.setScale(vec3(*prop.getScale()))
            self.mgr.repaint()

        def setRotation():
            rep.setRotation(*prop.getRotation())
            self.mgr.repaint()

        prop.transx.valueChanged.connect(setPosition)
        prop.transy.valueChanged.connect(setPosition)
        prop.transz.valueChanged.connect(setPosition)

        prop.scalex.valueChanged.connect(setScale)
        prop.scaley.valueChanged.connect(setScale)
        prop.scalez.valueChanged.connect(setScale)

        prop.yaw.valueChanged.connect(setRotation)
        prop.pitch.valueChanged.connect(setRotation)
        prop.roll.valueChanged.connect(setRotation)

        return prop

    def addSceneObject(self,obj):
        '''
        Called by the manager when a scene object is added, returns the properties dialog box and an update function.
        If no 'self.win' object is present, return (None,None).
        '''
        if self.win:
            prop=self.win.callFuncUIThread(lambda:self.createObjPropBox(obj))
            return prop,self.updateObjPropBox
        else:
            return None,None

    def addSceneObjectRepr(self,rep):
        '''
        Called by the manager when a representation is added, returns the properties dialog box, update function for
        that dialog, and a function to call when a representation is double-clicked (typically toggles visibility).
        If no 'self.win' object is present, return (None,None,None).
        '''
        if self.win:
            prop=self.win.callFuncUIThread(lambda:self.createReprPropBox(rep))
            return prop,self.updateReprPropBox,self._setReprVisibleCheckbox
        else:
            return None,None,None

    @delegatedmethod
    def applyMaterial(self,rep,mat,**kwargs):
        '''
        Apply material `mat' to the representation `rep', which may make internal copies of the material with differing
        properties. Users should not rely on changing materials to have an effect on representations after application.
        The argument `mat' must be a material object. The named argument `field' is used to specify what field to use
        for coloration or vector determination. The value of this argument should be a RealMatrix object containing
        the data field, or the name of the data field stored by the representation's SceneObject parent. 
        '''
        pass

    def objectMenuItem(self,obj,item):
        '''Called when the right-click menu for `obj' is clicked on item `item'. Override this to handle such events.'''
        pass

    def _getUIReprParams(self,obj,prop):
        '''
        Returns a pair `(args,kwargs)', where `args' is the list of position arguments and `kwargs' is the keyword
        argument dictionary, for the call obj.createRepr(). This method is called by _createReprButton() only and is
        used to create the arguments for the createRepr() from the UI.
        '''
        return [getReprTypeFromStrBox(prop.reprsBox)],{}

    def _createReprButton(self,obj,prop):
        @taskroutine('Create Representation')
        def _createRepr(obj,prop,task=None):
            args,kwargs=self._getUIReprParams(obj,prop)
            rep=obj.createRepr(*args,**kwargs)
            if rep:
                self.mgr.addSceneObjectRepr(rep)

        isEmptyScene=len(list(self.mgr.enumSceneObjectReprs()))==0
        self.mgr.addTasks(_createRepr(obj,prop))
        if isEmptyScene:
            self.mgr.addFuncTask(self.mgr.setCameraSeeAll)

    def _applyMaterialButton(self,rep,prop):
        matname=str(prop.matnameBox.currentText())
        self.applyMaterial(rep,self.mgr.getMaterial(matname),prop=prop)
        self.mgr.addFuncTask(prop.update)

    def _setReprVisibleCheckbox(self,rep):
        rep.setVisible(not rep.isVisible())
        self.win.setVisibilityIcon(rep,rep.isVisible())
        self.mgr.repaint()

    def _setParent(self,rep,prop):
        name=str(prop.parentBox.currentText())
        parent=first(r for r in self.mgr.enumSceneObjectReprs() if r.name==name)
        try:
            rep.setParent(parent)
            self.mgr.repaint()
        except:
            self.updateReprPropBox(rep,prop)


class MeshScenePlugin(ScenePlugin):
    '''
    Base plugin for all mesh scene object types. This isn't useful for image-based types because the algorithms are
    focused on doing mesh operations needed to create representations.
    '''

    def __init__(self,name):
        ScenePlugin.__init__(self,name)

    def getScriptCode(self,obj,**kwargs):
        configSection=kwargs.get('configSection',False)
        namemap=kwargs.get('namemap',{})
        varname=namemap[obj]
        script=''
        args={'varname':varname}

        if isinstance(obj,(MeshSceneObjectRepr,TDMeshSceneObjectRepr)):
            if configSection:
                if isinstance(obj,SceneObjectRepr):
                    setField=kwargs.get('setField',True)
                    field=obj.getSelectedFieldName()
                    if setField and field not in (None,''):
                        script+='%(varname)s.setDataField("%(fieldname)s")\n'
                        args['fieldname']=field

                        funcs=obj.getDataFuncMap()
                        if len(funcs)>0:
                            script+='%(varname)s.setDataFuncs(**%(datafuncs)r)\n'
                            args['datafuncs']=funcs

                        minf,maxf=obj.getSelectedFieldRange()
                        if minf!=None:
                            script+='%(varname)s.setSelectedFieldRange(%(minf)r,%(maxf)r)\n'
                            args['minf']=minf
                            args['maxf']=maxf

                script+=ScenePlugin.getScriptCode(self,obj,**kwargs)
            else:
                robj=first(obj.enumSubreprs())

                args.update({
                    'pname':namemap[obj.parent],
                    'reprtype':robj.reprtype,
                    'refine':robj.refine,
                    'drawInternal':robj.drawInternal,
                    'externalOnly':robj.externalOnly,
                    'matname':namemap.get(obj.getMaterialName(),'Default'),
                    'kwargs':(',**'+repr(robj.kwargs) if len(robj.kwargs)>0 else '')
                })

                script='%(varname)s=%(pname)s.createRepr(ReprType._%(reprtype)s,%(refine)r,drawInternal=%(drawInternal)r,externalOnly=%(externalOnly)r,matname="%(matname)s"%(kwargs)s)'

            return Utils.setStrIndent(script % args).strip()+'\n'
        else:
            return ScenePlugin.getScriptCode(self,obj,**kwargs)

    def getIcon(self,obj):
        return IconName.Mesh

    def getObjFiles(self,obj):
        '''By default there is no way to save a mesh but they are savable so return [] instead of None.'''
        return []

    def updateObjPropBox(self,obj,prop):
        ScenePlugin.updateObjPropBox(self,obj,prop)
        self._setParamPanel(obj,prop)

    def updateReprPropBox(self,rep,prop):
        ScenePlugin.updateReprPropBox(self,rep,prop)

        # if rep.reprtype in (ReprType._bbline,ReprType._bbpoint,ReprType._bbplane):
        #   prop.widthBox.setValue(rep.getWidth()[0])
        #   prop.heightBox.setValue(rep.getHeight()[0])

        fieldname=rep.getSelectedFieldName()
        fillList(prop.datafieldBox,rep.getFieldNames(),fieldname,'None')

        fillList(prop.alphafuncBox,(i[0] for i in UnitFunc),rep.getDataFunc('alphafunc'))
        fillList(prop.valfuncBox,(i[0] for i in ValueFunc),rep.getDataFunc('valfunc'))
#       fillList(prop.vecfuncBox,(i[0] for i in VecFunc),rep.getDataFunc('vecfunc'))

        if hasattr(prop,'internalCheckbox'):
            setChecked(rep.isDrawInternal(),prop.internalCheckbox)

        self._updateMinMaxPropFields(rep,prop,False)

    def _updateMinMaxPropFields(self,rep,prop,recalcMinMax=True):
        with VisualizerUI.signalBlocker(prop.minvalBox,prop.maxvalBox):
            field=rep.getDataField()

            if field!=None:
                selminf,selmaxf=rep.getSelectedFieldRange()

                if selminf==None or recalcMinMax:
                    #if isIterable(field):
                    #   field=first(field)

                    valfunc=rep.getDataFunc('valfunc',ValueFunc)

                    #minf,maxf=calculateFieldMinMax(field,valfunc)

                    #minf,maxf=minmax((calculateFieldMinMax(ff,valfunc) for ff in toIterable(field)),ranges=True)

                    minf,maxf=calculateFieldMinMax(toIterable(field),valfunc)

                    fdiff=abs(minf-maxf) if minf!=None and maxf!=None else 0
                    prop.minvalBox.setRange(minf-fdiff*10,maxf+fdiff*10)
                    prop.maxvalBox.setRange(minf-fdiff*10,maxf+fdiff*10)
                    selminf=minf # clamp(selminf,minf,maxf)
                    selmaxf=maxf # clamp(selmaxf,minf,maxf)
                    rep.setSelectedFieldRange(selminf,selmaxf)
            else:
                selminf=0
                selmaxf=0

            prop.minvalBox.setEnabled(field!=None)
            prop.maxvalBox.setEnabled(field!=None)
            prop.minvalBox.setValue(selminf)
            prop.maxvalBox.setValue(selmaxf)

    def createObjPropBox(self,obj):
        prop=ScenePlugin.createObjPropBox(self,obj)

        label,refine=prop.addReprOption('refineBox','Refinement',CustomUIType._int,0,1000)

        prop.parammap={}
        reprs=obj.getReprTypes()

        for r in reprs:
            params=obj.getReprParams(r)
            if len(params)>0:
                panel=ParamPanel(params)
                panel.setParamChangeFunc(lambda n,v,p=panel:self.updateReprParamPanel(obj,p,n,v))
                prop.parammap[r]=panel

        prop.reprsBox.currentIndexChanged.connect(lambda i : self._setParamPanel(obj,prop))
        prop.setParamPanel(prop.parammap.get(reprs[0],None)) # ensure the parameters panel appears for the first item

        return prop

    def _setParamPanel(self,obj,prop):
        reprtype=getReprTypeFromStrBox(prop.reprsBox)
        prop.setParamPanel(prop.parammap.get(reprtype,None))
        panel=prop.getParamPanel()
        if panel:
            panel.fillStrList(obj.getFieldNames(),ParamType._field,'None')
            panel.fillStrList([v[0] for v in ValueFunc],ParamType._valuefunc,'None')
            panel.fillStrList([v[0] for v in VecFunc],ParamType._vecfunc,'None')
            panel.fillStrList([v[0] for v in UnitFunc],ParamType._unitfunc,'None')

            # if this is the first time the panel was update, call updateReprParamPanel to fill in the boxes correctly
            if panel.isFirstUpdate:
                panel.isFirstUpdate=False
                self.updateReprParamPanel(obj,panel,'field',None)

    def updateReprParamPanel(self,obj,panel,name,val):
        if name in ('field','valfunc') and panel.hasParam('field') and panel.hasParam('valfunc'):
            valfunc=panel.getParam('valfunc')

            fields=obj.getDataField(panel.getParam('field'))

            if fields==None:
                return

#           fields=toIterable(fields)

            try:
#               minv,maxv=calculateFieldMinMax(fields[0],valfunc)
#               for f in fields[1:]:
#                   minv1,maxv1=calculateFieldMinMax(f,valfunc)
#                   minv=min(minv,minv1)
#                   maxv=max(maxv,maxv1)

                minv,maxv=calculateFieldMinMax(toIterable(fields),valfunc)

                if panel.hasParam('minv'):
                    VisualizerUI.setSpinBox(panel.getParamUI('minv'),minv,maxv,(maxv-minv)*0.01)
                    panel.setParam('minv',minv)

                if panel.hasParam('maxv'):
                    VisualizerUI.setSpinBox(panel.getParamUI('maxv'),minv,maxv,(maxv-minv)*0.01)
                    panel.setParam('maxv',maxv)
            except:
                pass

    def createReprPropBox(self,rep):
        prop=ScenePlugin.createReprPropBox(self,rep)

        if not rep.isExternalOnly():
            label,c=prop.addProperty('internalCheckbox','Show Internal',CustomUIType._checkbox)
            c.stateChanged.connect(lambda:self._setReprInternalCheckbox(rep))

        #label,specbox=prop.addMaterialOption('specnameBox','Spectrum',CustomUIType._strlist) # TODO: introduce spectrum selection?
        label,fieldbox=prop.addMaterialOption('datafieldBox','Data Field',CustomUIType._strlist)
        label,valfunc=prop.addMaterialOption('valfuncBox','Value Func',CustomUIType._strlist)
        label,b=prop.addMaterialOption('alphafuncBox','Alpha Func',CustomUIType._strlist)

        label,ll=prop.addMaterialOption('vrlabel','Value Range',CustomUIType._label)
        label,minb=prop.addMaterialOption('minvalBox','Min',CustomUIType._real,-1,-1,0.01)
        label,maxb=prop.addMaterialOption('maxvalBox','Max',CustomUIType._real,-1,-1,0.01)

        minb.setEnabled(False)
        maxb.setEnabled(False)
        minb.valueChanged.connect(lambda v:rep.setSelectedFieldRange(v,None))
        maxb.valueChanged.connect(lambda v:rep.setSelectedFieldRange(None,v))

        fieldbox.currentIndexChanged.connect(lambda i:rep.setDataField(str(prop.datafieldBox.currentText())))
        fieldbox.currentIndexChanged.connect(lambda i:self._updateMinMaxPropFields(rep,prop))

        valfunc.currentIndexChanged.connect(lambda i:rep.setDataFuncs(valfunc=str(prop.valfuncBox.currentText())))
        valfunc.currentIndexChanged.connect(lambda i:self._updateMinMaxPropFields(rep,prop))

        return prop

    @taskroutine('Calculating Element Properties')
    def calculateExtAdj(self,obj,task):
        MeshAlgorithms.calculateElemExtAdj(obj.datasets[0],task=task)

        # copy over adjacency and external information from first dataset if present
        for ds in obj.datasets:
            ds0=obj.datasets[0]
            for n in ds.getIndexNames():
                adjname=n+SceneUtils.MatrixType.adj[1]
                extname=n+SceneUtils.MatrixType.external[1]

                if ds0.hasIndexSet(adjname):
                    ds.setIndexSet(ds0.getIndexSet(adjname))

                if ds0.hasIndexSet(extname):
                    ds.setIndexSet(ds0.getIndexSet(extname))

    def createReprDataset(self,dataset,reprtype,name,refine,externalOnly,task,**kwargs):
        '''
        Creates a representation dataset using the algorithm defined in ReprType by default. If another algorithm
        routine is provided as a keyword argument 'algorithm', this will be used instead. This algorithm must adhere
        to the protocol defined in MeshAlgorithms.
        '''
        if reprtype not in ReprType or not ReprType[reprtype][1]:
            raise ValueError('Unsupported representation type '+reprtype)

        if dataset.getNodes().n()==0:
            raise ValueError('Cannot create representation from dataset with no nodes')

        algorithm=kwargs.get('algorithm',ReprType[reprtype][1]) # use the given algorithm if present, defaulting to that in ReprType

        return algorithm(dataset,name,refine,externalOnly,task,**kwargs)

    def createRepr(self,obj,reprtype,refine=0,drawInternal=False,externalOnly=True,matname='Default',**kwargs):
        f=Future()

        @taskroutine('Creating Representation')
        @timing
        def createReprTask(task):
            with f:
                obj.reprcount+=1

                name=obj.name+' '+ReprType[reprtype][0]+str(obj.reprcount)

                if len(obj.datasets)==1:
                    ds=self.createReprDataset(obj.datasets[0],reprtype,name,refine,externalOnly,task,**kwargs)
                    rep=MeshSceneObjectRepr(obj,reprtype,obj.reprcount,refine,ds,obj.datasets[0],drawInternal,externalOnly,matname,**kwargs)
                else:
                    subreprs=[]
                    srcdsmap={}

                    for i,dds in enumerate(obj.datasets):
                        name='%s %s %i [%i/%i]' %(obj.name,ReprType[reprtype][0],obj.reprcount,i+1,len(obj.datasets))

                        ddsorig=dds.meta(SceneUtils.StdProps._isdsclone)
                        if ddsorig!='' and ddsorig in srcdsmap:
                            dsorig,origindices=srcdsmap[ddsorig]
                            dataset=dsorig.clone(name,True,True,False)

                            for field in dds.fields.values():
                                dataset.setDataField(field)

                            ds=dataset,origindices
                        else:
                            ds=self.createReprDataset(dds,reprtype,name,refine,externalOnly,task,**kwargs)

                        srcdsmap[ddsorig]=ds

                        rep=MeshSceneObjectRepr(obj,reprtype,obj.reprcount,refine,ds,dds,drawInternal,externalOnly,matname,**kwargs)
                        rep.name+=' [%i/%i]'%(i+1,len(obj.datasets))
                        subreprs.append(rep)

                    rep=TDMeshSceneObjectRepr(subreprs,obj,reprtype,obj.reprcount,matname)
                    
                if matname!='Default':
                    self.applyMaterial(rep,matname,**kwargs)

                obj.reprs.append(rep)
                f.setObject(rep)

        errlist=ParamDef.validateArgMap(self.getReprParams(obj,reprtype),kwargs)
        if len(errlist)>0:
            raise ValueError('Representation Parameter Error:\n   '+'\n   '.join(errlist))

        tasks=[self.calculateExtAdj(obj),createReprTask()]
        
        return self.mgr.runTasks(tasks,f)

    @delegatedmethod
    def loadDataField(self,obj,*args,**kwargs):
        '''
        Loads a data field for the given 'obj' SceneObject instance. This is typically called by the method of 'obj'
        of the same name rather than directly. This returns a Future which will contain the loaded data field. The
        stated arguments for this method may change in derived plugin definitions.
        '''
        pass

    def getReprParams(self,obj,reprtype):
        assert reprtype in ReprType, 'Unknown repr type: '+str(reprtype)
        maxdim=max([0]+[ElemType[e].dim for e in obj.elemTypes()])
        params=[]

        if maxdim==3 and reprtype not in (ReprType._isosurf,ReprType._isoline):
            params.append(ParamDef('externalOnly','External Only',ParamType._bool,True))

        if reprtype==ReprType._volume:
            params.append(ParamDef('doubleSided','Double Sided',ParamType._bool,True))
        elif reprtype==ReprType._cylinder:
            params+= [
                ParamDef('radrefine','Rad Refine',ParamType._int,0,0,99,1),
                ParamDef('radius','Radius',ParamType._real,0.0,0.0,99999.0,0.1),
                ParamDef('field','Radius Field',ParamType._field)
            ]
        elif reprtype == ReprType._glyph:
            rad=SceneUtils.BoundBox(obj.datasets[0].getNodes()).radius
            params+=[
                ParamDef('glyphname','Glyph',ParamType._strlist,['sphere','arrow','cube'],notNone=True),
                ParamDef('dfield','Dir Field',ParamType._field),
                ParamDef('vecfunc','Vector Func',ParamType._vecfunc),
                ParamDef('sfield','Scale Field',ParamType._field),
                ParamDef('scalefunc','Scale Func',ParamType._vecfunc),
                ParamDef('glyphscale','Scale',ParamType._vec3,[rad*0.01]*3,[0.0000001]*3,[rad*2]*3,[rad*0.01]*3),
            ]
        elif reprtype == ReprType._isosurf:
            params+=[
                ParamDef('field','Data Field',ParamType._field,notNone=True),
                ParamDef('valfunc','Value Func',ParamType._valuefunc,notNone=True),
                ParamDef('minv','Min Value',ParamType._real,0.0,None,None,1.0),
                ParamDef('maxv','Max Value',ParamType._real,0.0,None,None,1.0),
                ParamDef('numitervals','Num Intervals',ParamType._int,1,1,999,1),
                ParamDef('vals','Value List',ParamType._str),
            ]
        elif reprtype == ReprType._isoline:
            params+=[
                ParamDef('field','Data Field',ParamType._field,notNone=True),
                ParamDef('valfunc','Value Func',ParamType._valuefunc,notNone=True),
                ParamDef('minv','Min Value',ParamType._real,0.0,None,None,1.0),
                ParamDef('maxv','Max Value',ParamType._real,0.0,None,None,1.0),
                ParamDef('radius','Line Radius',ParamType._real,1.0,0.000001,9999.0,0.1),
                ParamDef('numitervals','Num Intervals',ParamType._int,1,1,999,1),
                ParamDef('vals','Value List',ParamType._str),
            ]
#       elif reprtype == ReprType._ribbon:
#           params+=[
#               ParamDef('rangeinds','Range Indices',ParamType._bool,True),
#               ParamDef('maxlen','Max Length',ParamType._real,0.0,1.0,9999,0.0),
#           ]

#       elif reprtype in (ReprType._bbpoint,ReprType._bbline,ReprType._bbplane):
#           rad=BoundBox(obj.datasets[0].getNodes()).radius
#           params+=[
#               ParamDef('width','Width',ParamType._real,rad*0.01,0,rad*2,rad*0.01),
#               ParamDef('height','Height',ParamType._real,rad*0.01,0,rad*2,rad*0.01),
#               ParamDef('field','Vector Field',ParamType._field),
#               ParamDef('vecfunc','Vector Func',ParamType._vecfunc),
#           ]

        if ReprType[reprtype][3] and maxdim>=2: # if point type, add Poisson distribution options
            params.append(ParamDef('usePoisson','Use Poisson Distribution',ParamType._bool,False))

        return params

    def getReprTypes(self,obj):
        elemtypes=obj.elemTypes()
        maxdim=max([0]+[ElemType[e].dim for e in elemtypes]) # maximal spatial dimensions for the stored index sets
        reprs=[ReprType._node,ReprType._point]

        if maxdim>0:
            reprs.append(ReprType._line)

        if maxdim>1:
            reprs.append(ReprType._volume)

        if maxdim>0:
            reprs.append(ReprType._cylinder)

        if maxdim>1:
            reprs.append(ReprType._isoline)

        if maxdim>2:
            reprs.append(ReprType._isosurf)

        reprs.append(ReprType._glyph)

        if len(obj.datasets)>1 and ElemType._Line1NL in elemtypes:
            reprs.append(ReprType._ribbon)

#       reprs.append(ReprType._bbpoint)
#       reprs.append(ReprType._bbline)
#       reprs.append(ReprType._bbplane)

        return reprs

    def getMenu(self,obj):
        if not isinstance(obj,MeshSceneObject):
            return None,None

        reprs=self.getReprTypes(obj)
        menu=[obj.getName(),ReprType.node[0],ReprType.point[0]]
        if ReprType._line in reprs:
            menu.append(ReprType.line[0])
        if ReprType._volume in reprs:
            menu.append(ReprType.volume[0])

        return menu,self.objectMenuItem

    def objectMenuItem(self,obj,item):
        @taskroutine('Create Representation')
        def _createRepr(obj,item,task=None):
            rep=obj.createRepr(item)
            if rep:
                self.mgr.addSceneObjectRepr(rep)
                if len(list(self.mgr.enumSceneObjectReprs()))==1:
                    self.mgr.setCameraSeeAll()

        item=getReprTypeFromStrBox(item)
        if item!=None:# in (ReprType._node,ReprType._point,ReprType._line,ReprType._volume):
            self.mgr.addTasks(_createRepr(obj,item))

    @timing
    def applyMaterial(self,rep,mat,**kwargs):
        @taskroutine('Apply Material')
        @timing
        def applyMaterialTask(rep,mat,useSpectrum,task):
            '''Apply the material `mat' the representation object `rep', using spectrum colors if `useSpectrum'.'''
            if isinstance(mat,str):
                mat=self.mgr.getMaterial(mat)

            valfunc=rep.getDataFunc('valfunc',ValueFunc)
            alphafunc=rep.getDataFunc('alphafunc',UnitFunc)
            rep.setMaterialName(mat.getName())
            isTransMat=mat.isTransparentColor()

            defaultcol=mat.getDiffuse()
            if mat.usesInternalAlpha():
                defaultcol=color(defaultcol.r(),defaultcol.g(),defaultcol.b(),mat.getAlpha())
                #defaultcol.a(mat.getAlpha())

            # collect only mesh repr objects which have nodes
            reprs=[r for r in rep.enumSubreprs() if isinstance(r,MeshSceneObjectRepr) and r.nodes.n()>0]

            fields=toIterable(rep.getDataField())
            minv,maxv=rep.getSelectedFieldRange()

            if minv==None and fields and all(fields):
                minv,maxv=calculateFieldMinMax(fields,valfunc)
                rep.setSelectedFieldRange(minv,maxv)

            for r in reprs:
                nodes=r.nodes
                nodecolors=r.nodecolors
                nodeprops=r.nodeprops
                origindices=r.origindices
                parentdataset=r.parentdataset
                field=r.getDataField()
                #fields=[field]*max(1,len(r.origindices))
                #minv,maxv=r.getSelectedFieldRange()

                #useFieldTopo=field!=None and (field.meta(StdProps._topology)!='' or field.meta(StdProps._spatial)!='')

                #if minv==None and field!=None:
                #   minv,maxv=calculateFieldMinMax(field,valfunc)
                #   r.setSelectedFieldRange(minv,maxv)

                if field!=None and (mat.numAlphaCtrls()>0 or mat.numSpectrumValues()>0) and useSpectrum:
                    isTransR=MeshAlgorithms.calculateDataColoration(mat,parentdataset,nodecolors,nodes,nodeprops,origindices,[field],minv,maxv,valfunc,alphafunc,task)
                    #if len(origindices)>0 and useFieldTopo:
                    #   calculateDataColoration(mat,parentdataset,nodecolors,nodes,nodeprops,origindices,[field],minv,maxv,valfunc,alphafunc,task)
                    #elif field.n()==nodes.n(): # per-node data field
                    #   calculatePerNodeColoration(mat,nodes,nodecolors,field,minv,maxv,valfunc,alphafunc,task)
                else:
                    nodecolors.fill(defaultcol)
                    isTransR=defaultcol.a()<1.0

                isTransMat=isTransMat or isTransR
                #isTransMat=isTransMat or any(nodecolors.getAt(i).a()<1.0 for i in xrange(nodecolors.n()))

            rep.setTransparent(isTransMat) # set whether this representation is transparent or not, ie. what render queue it's in

            if rep.isInScene():
                self.mgr.updateSceneObjectRepr(rep)


        # get functions and field parameters from the property dialog used to apply this material or from `kwargs'
        prop=kwargs.get('prop',None)
        if prop: # if the properties box object is given then this method was called through the UI and its values take priority
            valfunc=str(prop.valfuncBox.currentText())
            alphafunc=str(prop.alphafuncBox.currentText())
            datafield=str(prop.datafieldBox.currentText())
            minfield,maxfield=None,None
        else:
            # If the rep object has a data func specified but this isn't present in the keyword args, add it to the
            # map; this ensures that a func value present in rep, but not overridden by the keyword args, will be used.
            funcmap=rep.getDataFuncMap()
            for k in funcmap:
                if k not in kwargs:
                    kwargs[k]=funcmap[k]

            # choose the values for these funcs from the keyword args with coherent defaults if they aren't present
            valfunc=Future.get(kwargs.get('valfunc',ValueFunc._Average))
            alphafunc=Future.get(kwargs.get('alphafunc',UnitFunc._One))
            datafield=Future.get(kwargs.get('field',None))
            minfield=Future.get(kwargs.get('minfield',None))
            maxfield=Future.get(kwargs.get('maxfield',None))

        rep.setDataFuncs(valfunc=valfunc,alphafunc=alphafunc) # set the field-value-to-unit-value and alpha functions

        if datafield not in (None,'None'): # set the data field if specified
            rep.setDataField(datafield)

        if minfield!=None or maxfield!=None: # set the field range only if specified
            rep.setSelectedFieldRange(minfield,maxfield)

        self.mgr.runTasks(applyMaterialTask(rep,mat,kwargs.get('useSpectrum',True)))

    def _getUIReprParams(self,obj,prop):
        reprtype=getReprTypeFromStrBox(prop.reprsBox)
        refine=int(prop.refineBox.value())
        params=prop.getParamPanel()
        conf=params.getParamMap() if params else {}

        return [reprtype,refine],conf

    def _setReprInternalCheckbox(self,rep):
        rep.setDrawInternal(not rep.isDrawInternal())
        self.mgr.addFuncTask(lambda:self.mgr.updateSceneObjectRepr(rep),'Update Representation')


class ImageScenePlugin(ScenePlugin):
    def __init__(self,name):
        ScenePlugin.__init__(self,name)

    def getIcon(self,obj):
        return IconName.Image

    def getObjFiles(self,obj):
        '''By default there is no way to save an image but they are savable so return [] instead of None.'''
        return []

    def getReprTypes(self,obj):
        types=[ReprType._imgstack]

        if not obj.is2D:
            types.append(ReprType._imgvolume)

        if obj.isTimeDependent:
            types.append(ReprType._imgtimestack)
            if not obj.is2D:
                types.append(ReprType._imgtimevolume)

        return types

    def getReprParams(self,obj,reprtype):
        assert reprtype in ReprType, 'Unknown repr type: '+str(reprtype)
        params=[]
        params.append(ParamDef('isGreyscale','Greyscale Image',ParamType._bool,True))

        return params

    def createObjPropBox(self,obj):
        prop=ScenePlugin.createObjPropBox(self,obj)

        prop.parammap={}
        reprs=obj.getReprTypes()

        for r in reprs:
            params=obj.getReprParams(r)
            if len(params)>0:
                panel=ParamPanel(params)
                prop.parammap[r]=panel

        prop.reprsBox.currentIndexChanged.connect(lambda i : self._setParamPanel(obj,prop))
        prop.setParamPanel(prop.parammap.get(reprs[0],None)) # ensure the parameters panel appears for the first item

        _,prop.ctcheck=VisualizerUI.addCustomUIRow(prop.layout(),1,CustomUIType._checkbox,'ctCheck','Is CT Image')
        prop.ctcheck.clicked.connect(lambda i:self.setCTImageRange(obj,i))

        #obj.histogram=ImageAlgorithms.calculateImageStackHistogram(obj,*ctImageRange) # pre-calculate and store the histogram for later possible use
        #self.setCTImageRange(obj,isCTImageSeries(hist=obj.histogram))

        return prop

    def _setParamPanel(self,obj,prop):
        reprtype=getReprTypeFromStrBox(prop.reprsBox)
        prop.setParamPanel(prop.parammap.get(reprtype,None))

    def _getUIReprParams(self,obj,prop):
        reprtype=getReprTypeFromStrBox(prop.reprsBox)
        params=prop.getParamPanel()
        conf=params.getParamMap() if params else {}

        return [reprtype],conf

    @delegatedmethod
    def setCTImageRange(self,obj,isCT):
        if isCT:
            obj.imagerange=tuple(ctImageRange)
        else:
            obj.imagerange=None

    def getMenu(self,obj):
        if not isinstance(obj,ImageSceneObject):
            return None,None

        menu=[obj.getName(),ReprType.imgstack[0]]
        if not obj.is2D:
            menu.append(ReprType.imgvolume[0])

        if obj.isTimeDependent:
            menu.append(ReprType.imgtimestack[0])
            if not obj.is2D:
                menu.append(ReprType.imgtimevolume[0])

        return menu,self.objectMenuItem

    def objectMenuItem(self,obj,item):
        @taskroutine('Create Representation')
        def _createRepr(obj,item,task=None):
            rep=obj.createRepr(item)
            if rep:
                self.mgr.addSceneObjectRepr(rep)
                if len(list(self.mgr.enumSceneObjectReprs()))==1:
                    self.mgr.setCameraSeeAll()

        item=getReprTypeFromStrBox(item)
        if item in ReprType:
            self.mgr.addTasks(_createRepr(obj,item))

    def updateObjPropBox(self,obj,prop):
        ScenePlugin.updateObjPropBox(self,obj,prop)
        with VisualizerUI.signalBlocker(prop.ctcheck):
            prop.ctcheck.setChecked(obj.imagerange==ctImageRange)

    def updateReprPropBox(self,rep,prop):
        ScenePlugin.updateReprPropBox(self,rep,prop)

        fillList(prop.specnameBox,self.mgr.listSpectrumNames(),prop.specnameBox.currentText())

        setChecked(rep.usesDepthCheck(),prop.depthCheck)
        setChecked(rep.usesDepthWrite(),prop.depthWrite)
        setChecked(rep.usesTexFiltering(),prop.texFilter)

    def createReprPropBox(self,rep):
        prop=ScenePlugin.createReprPropBox(self,rep)

        prop.spectrumBox.setVisible(True)

        label,check=prop.addProperty('depthCheck','Depth Check',CustomUIType._checkbox)
        label,write=prop.addProperty('depthWrite','Depth Write',CustomUIType._checkbox)
        label,texfilter=prop.addProperty('texFilter','Texture Filtering',CustomUIType._checkbox)

        if isinstance(rep,ImageSeriesRepr) and rep.getNumStackSlices()>1:
            label,selslice=prop.addProperty('selectSlice','Select Slice',CustomUIType._checkbox)
            label,slider=prop.addProperty('sliceSlider','Slice',CustomUIType._hslider,minval=0,maxval=rep.getNumStackSlices()-1)
            slider.setTickInterval(1)

            def _setSelectedSlice(*args):
                chosen=slider.sliderPosition() if selslice.isChecked() else None
                rep.setChosenSlice(chosen)
                self.mgr.repaint()

            selslice.toggled.connect(_setSelectedSlice)
            slider.actionTriggered.connect(_setSelectedSlice)

        check.toggled.connect(lambda i:rep.useDepthCheck(i) or self.mgr.repaint())
        write.toggled.connect(lambda i:rep.useDepthWrite(i) or self.mgr.repaint())
        texfilter.toggled.connect(lambda i:rep.useTexFiltering(i) or self.mgr.repaint())

        prop.spectrum=SceneComponents.SpectrumWidget(rep.enumInternalMaterials,self.mgr,prop)
        layout=prop.spectrumBox.layout()
        layout.addWidget(prop.spectrum)

        def _setSpectrum():
            spec=self.mgr.getSpectrum(prop.specnameBox.currentText())
            if spec:
                rep.applySpectrum(spec)
                prop.spectrum.repaint()
                self.mgr.repaint()

        prop.setSpecButton.clicked.connect(_setSpectrum)

        return prop

    def applyMaterial(self,rep,mat,**kwargs):
        assert mat!=None
        rep.imgmat=mat
        rep.matname=mat.getName()
        rep.copySpec=mat.numSpectrumValues()>0 or mat.numAlphaCtrls()>0
        self.mgr.updateSceneObjectRepr(rep)

    def createRepr(self,obj,reprtype,**kwargs):
        if reprtype not in (ReprType._imgstack,ReprType._imgtimestack,ReprType._imgvolume,ReprType._imgtimevolume):
            raise ValueError('Unsupported representation type '+reprtype)

        f=Future()
        isVolume=reprtype in (ReprType._imgvolume,ReprType._imgtimevolume)
        repconstr=ImageVolumeRepr if isVolume else ImageSeriesRepr # choose a constructor
        basematname='BaseImage' if isVolume else 'BaseImage2D' # choose one of the base materials known to have the right shaders
        imgmaterial=kwargs.pop('imgmat',self.mgr.getMaterial(basematname)) # choose material defaulting to one of the base materials
        defaultformat=TF_ALPHALUM8 if kwargs.pop('isGreyscale',True) else TF_RGBA32 # set texture format to be 8bit greyscale or RGBA color
        kwargs['texformat']=kwargs.get('texformat',None) or defaultformat

        @taskroutine('Creating Representation')
        @timing
        def createReprTask(task):
            with f:
                task.setMaxProgress(0)
                rep=repconstr(obj,reprtype,obj.reprcount,imgmaterial,**kwargs)
                obj.reprs.append(rep)
                obj.reprcount+=1
                f.setObject(rep)

        return self.mgr.runTasks(createReprTask(),f)

    def createSceneObject(self,name,images,source=None,isTimeDependent=None):
        return ImageSceneObject(name,source,images,self,isTimeDependent)

    @delegatedmethod
    def clone(self,obj,name):
        return self.createSceneObject(name,[i.clone() for i in obj.images],obj.source,obj.isTimeDependent)

    @delegatedmethod
    def cropXY(self,obj,name,minx,miny,maxx,maxy):
        return self.createSceneObject(name,[i.crop(minx,miny,maxx,maxy) for i in obj.images],obj.source,obj.isTimeDependent)

    @delegatedmethod
    def extractTimesteps(self,obj,name,indices=None,timesteps=None,setTimestep=False):
        '''
        Create a clone of 'obj' containing only the images of the selected timesteps. Exactly one of `indices'
        or `timesteps' must be non-None. If `indices' is not None, it must be a list of indices of timesteps to extract,
        eg. to extract the first timestep a value of [0] would be used. If `timesteps' is not None, it must be a list
        of times for each of which the closest timestep is chosen, eg. a value of [30] extracts the timestep closest
        to 30. If `setTimestep' is True then the time values for the extract images are changed to ascending integer
        values if `indices' is used or to that specified in `timesteps' if `timesteps' is used. This ensures duplicate
        timesteps have different time values.
        '''
        assert (indices!=None)!=(timesteps!=None)

        inds=obj.getTimestepIndices()
        isTimeDependent=None if obj.isTimeDependent else False # if `obj' is time dependent, need to evaluate if returned object is
        clonedimages=[]

        if indices!=None:
            for ts,ind in enumerate(indices):
                images=[obj.images[i].clone() for i in inds[ind][1]]
                clonedimages+=images
                if setTimestep:
                    for img in images:
                        img.timestep=ts
        else:
            for ts in timesteps:
                _,ilist=min( (abs(ts-ts1),ilist) for ts1,ilist in inds ) # find the index list nearest in time to ts
                images=[obj.images[i].clone() for i in ilist]
                clonedimages+=images
                if setTimestep:
                    for img in images:
                        img.timestep=ts

        return self.createSceneObject(name,clonedimages,obj.source,isTimeDependent)

    def createImageStackObject(self,name,width,height,slices,timesteps=1,pos=vec3(),rot=rotator(),spacing=vec3(1)):
        '''Create a blank image object with each timestep ordered bottom-up with integer timesteps.'''
        src=(width,height,slices, timesteps,pos,rot,spacing)
        images=ImageAlgorithms.generateImageStack(width,height,slices,timesteps,pos,rot,spacing,name)
        return self.createSceneObject(name,images,src,timesteps>1)

    @delegatedmethod
    def createRespacedObject(self,obj,name,spacing=vec3(1)):
        '''Create an image object occupying the same space as `obj' with voxel dimensions given by `spacing'.'''
        trans=obj.getVolumeTransform()
        t=obj.getTimestepList()
        w,h,d=map(Utils.fcomp(int,abs,round),trans.getScale()/spacing)

        imgobj=obj.plugin.createImageStackObject(name,w,h,d,len(t),trans.getTranslation(),trans.getRotation(),spacing)
        imgobj.setTimestepList(t)
        return imgobj

    def createIntersectObject(self,name,objs,timesteps=1,spacing=vec3(1)):
        assert all(not o.is2D for o in objs)

        trans=objs[0].getVolumeTransform()
        inv=trans.inverse()
        mincorner=vec3(0)
        maxcorner=vec3(1)
        #corners=[]

        for o in objs[1:]:
            bb=SceneUtils.BoundBox(inv*c for c in o.getVolumeCorners())
            mincorner.setMaxVals(bb.minv)
            maxcorner.setMinVals(bb.maxv)

        assert all(minv<maxv for minv,maxv in zip(mincorner,maxcorner))

        w,h,d=map(int,(maxcorner-mincorner)*(trans.getScale().abs()/spacing))
        return self.createImageStackObject(name,w,h,d,timesteps,trans*mincorner,trans.getRotation(),spacing)

    def createTestImage(self,w,h,d,timesteps=1,pos=vec3(),rot=rotator(),spacing=vec3(1)):
        '''Generate a test image object with the given dimensions (w,h,d) with image values range (minv,maxv).'''
        images=ImageAlgorithms.generateTestImageStack(w,h,d,timesteps,pos,rot,spacing)
        return self.createSceneObject('TestImage',images,(w,h,d,timesteps,pos,rot,spacing),timesteps>1)

    def createSequence(self,name,objs,timesteps=None):
        '''
        Create a time-dependent object from the static images `objs', each of which becomes a timestep in the result
        object. The timesteps for the result is `timesteps' or range(len(objs)) if given as None.
        '''
        timesteps=timesteps or range(len(objs))
        images=[]
        for t,o in zip(timesteps,objs):
            for i in o.images:
                i=i.clone()
                i.timestep=t
                images.append(i)

        return self.createSceneObject(name,images,objs[0].source,len(timesteps)>1)

    @timing
    def createObjectFromArray(self,name,array,interval=1.0,toffset=0,pos=vec3(),rot=rotator(),spacing=vec3(1),task=None):
        '''
        Create an image object from the 4D Numpy array and the given parameters, `interval' denoting timestep
        interval and `toffset' denoting time start point, both in ms. The dimensions of the numpy array represent
        width, height, depth (or number of slices), and time in that order. Depth or time may be 1, so an array of
        dimensions (X,Y,1,T) is a 2D time-dependent image series of dimensions (X,Y), while an array with (X,Y,Z,1)
        is a single volume of dimensions (X,Y,Z).
        '''
        shape=tuple(array.shape)+(1,1) # add extra dimensions to the shape to make a 4D shape
        shape=shape[:4] # clip extra dimensions off so that this is a 4D shape description
        width,height,slices,timesteps=shape
        datatype=np.dtype('<f8')
        array=array.astype(datatype).reshape(shape) # convert to little endian double and reshape into a 4D array

        obj=self.createImageStackObject(name,width,height,slices,timesteps,pos,rot,spacing)

        if task:
            task.setMaxProgress(slices*timesteps)

        for s,t in Utils.trange(slices,timesteps):
            if task:
                task.setProgress(t+s*timesteps+1)

            i=obj.images[s+t*slices]
            np.asarray(i.img)[:,:]=array[:,:,s,t].T # fill the array for this image by slicing the volume
            i.setMinMaxValues(*minmaxMatrixReal(i.img)) # set the min/max values for the array
            i.timestep=i.timestep*interval+toffset # set the timestep based off of the interval, offset, and default time number for this image

        if task:
            task.setProgress(slices*timesteps)

        return obj

    @delegatedmethod
    def getImageObjectArray(self,obj,datatype=float):
        '''
        Create a 4D Numpy array of type `datatype' from the image data in `obj'. This array is intended to be
        suitable as input to libraries for writing image files. The result is a dictionary with the following keys:
            array : numpy array, shape is (width,height,depth,timestep)
            pos : position in space
            spacing : pixel/voxel dimensions
            rot : spatial rotation
            toffset : time offset
            interval : time interval
        '''
        assert isinstance(obj,ImageSceneObject)
        assert len(obj.getOrientMap())==1, 'Cannot produce a array from non-stack image objects'

        with ImageAlgorithms.processImageNp(obj,False,datatype) as array:
            timesteps=obj.getTimestepList()
            trans=obj.getTransform()
            pos=trans.getTranslation()
            rot=trans.getRotation()
            spacing=trans.getScale().abs()*vec3(array.shape[0],array.shape[1],array.shape[2]-1).inv()
            toffset=timesteps[0]
            interval=Utils.avgspan(timesteps) if len(timesteps)>1 else 0
            
            return dict(pos=pos,spacing=spacing,rot=rot,array=array,toffset=toffset,interval=interval)


class CombinedScenePlugin(MeshScenePlugin,ImageScenePlugin):
    '''
    This plugin type combines mesh and image plugins into one, overriding common methods which call the appropriate
    inherited method depending on the type of the argument.
    '''
    def __init__(self,name):
        MeshScenePlugin.__init__(self,name)
        ImageScenePlugin.__init__(self,name)

    def _getSupertype(self,obj_or_rep):
        if isinstance(obj_or_rep,(MeshSceneObject,MeshSceneObjectRepr)):
            return MeshScenePlugin
        elif isinstance(obj_or_rep,(ImageSceneObject,ImageSceneObjectRepr)):
            return ImageScenePlugin
        else:
            return ScenePlugin

    def getIcon(self,obj):
        return self._getSupertype(obj).getIcon(self,obj)

    def getReprTypes(self,obj):
        return self._getSupertype(obj).getReprTypes(self,obj)

    def getReprParams(self,obj,reprtype):
        return self._getSupertype(obj).getReprParams(self,obj,reprtype)

    def getMenu(self,obj):
        return self._getSupertype(obj).getMenu(self,obj)

    def objectMenuItem(self,obj,item):
        return self._getSupertype(obj).objectMenuItem(self,obj,item)

    def _setParamPanel(self,obj,prop):
        return self._getSupertype(obj)._setParamPanel(self,obj,prop)

    def _getUIReprParams(self,obj,prop):
        return self._getSupertype(obj)._getUIReprParams(self,obj,prop)

    def updateObjPropBox(self,obj,prop):
        return self._getSupertype(obj).updateObjPropBox(self,obj,prop)

    def updateReprPropBox(self,rep,prop):
        return self._getSupertype(rep).updateReprPropBox(self,rep,prop)

    def createObjPropBox(self,obj):
        return self._getSupertype(obj).createObjPropBox(self,obj)

    def createReprPropBox(self,rep):
        return self._getSupertype(rep).createReprPropBox(self,rep)

    def applyMaterial(self,rep,mat,**kwargs):
        return self._getSupertype(rep).applyMaterial(self,rep,mat,**kwargs)

    def createRepr(self,obj,reprtype,**kwargs):
        return self._getSupertype(obj).createRepr(self,obj,reprtype,**kwargs)


