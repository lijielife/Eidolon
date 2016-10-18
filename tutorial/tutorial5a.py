
from eidolon import ElemType, ReprType, AxesType, vec3, PyDataSet, listToMatrix, generateHexBox, MeshSceneObject

# The following code will recreate the data loaded in tutorial5.py thus is an example of algorithmic mesh generation 

nodes,inds=generateHexBox(1,1,1) # Generate a box in the unit cube comprised of 8 hexahedra
nodes=listToMatrix(nodes,'Nodes') # use this helper function to convert Python lists to *Matrix types (Vec3Matrix in this case)
inds=listToMatrix(inds,'LinHex',ElemType._Hex1NL) # convert here as well specifying a matrix type Hex1NL

# construct a list of PyDataset objects which contain the node, topology, and field information for each timestep
dds=[]
for i in xrange(10):
	n1=nodes.clone() # clone the nodes
	n1.mul(vec3(1+i*0.1,1,1)) # scale the nodes in the X direction
	field=[n1.getAt(j).lenSq() for j in xrange(n1.n())]# generate a field defined as the squared length of each node 
	fieldmat=listToMatrix(field,'LenSq') # convert field, the matrix for each timestep has the same name "LenSq"
	dds.append(PyDataSet('ds%i'%i,n1,[inds],[fieldmat])) # create the dataset, each shares the matrix `inds' which is safe
	
obj=MeshSceneObject('LinBox',dds) # create the MeshSceneObject, note that this object's "plugin" attribute is None here
mgr.addSceneObject(obj) # add it to the scene, the manager will assign its plugin attribute to be the default mesh plugin

rep=obj.createRepr(ReprType._volume,10,externalOnly=True)
mgr.addSceneObjectRepr(rep)

mgr.setCameraSeeAll()
mgr.controller.setRotation(-0.6,0.6)
mgr.setAxesType(AxesType._cornerTR)

rep.applyMaterial(mgr.getMaterial('Rainbow'),field='LenSq') # shortcut way to apply the material with the chosen field
