
from eidolon import ElemType, ReprType, ValueFunc # import the symbols we'll need

rootdir=scriptdir+'/MeshData' 

# acquire the plugin and ask it to load a scene object
obj=CHeart.loadSceneObject(rootdir+'/linmesh_FE.X',rootdir+'/linmesh_FE.T',ElemType._Hex1NL)

# load a data field which has 1 value per line and no topology; the spatial topology of the scene object will be used as the field topology
df=obj.loadDataField(rootdir+'/linmesh_dist.D',1)

# add the object to the scene
mgr.addSceneObject(obj)

# create a volume representation which is not refined and represents the external surface only
rep=obj.createRepr(ReprType._line,0,externalOnly=True)

# add the representation to the scene
mgr.addSceneObjectRepr(rep)

mgr.setCameraSeeAll() # set the camera viewing parameters to match the object's size
mgr.controller.setRotation(1.0,1.0) # rotate the camera slightly

m=mgr.getMaterial('Rainbow') # acquire this pre-existing material
 
# create an isosurface set using the field `df' as the input and generating 10 planes roughly between the field's minimum and maxmimum values
rep=obj.createRepr(ReprType._isosurf,0,field=df,numitervals=10,minv=0.0,maxv=80.0,valfunc=ValueFunc.Magnitude)
mgr.addSceneObjectRepr(rep)

rep.applyMaterial(m,field=df)

# do the same with isolines but at a higher refinement value
rep=obj.createRepr(ReprType._isoline,5,field=df,numitervals=10,minv=0.0,maxv=80.0,radius=0.5,valfunc=ValueFunc.Magnitude)
mgr.addSceneObjectRepr(rep)

rep.applyMaterial(m,field=df)

