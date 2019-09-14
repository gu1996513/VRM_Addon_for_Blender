import bpy
class ICYP_OT_MAKE_MESH_FROM_BONE_ENVELOPES(bpy.types.Operator):
	bl_idname = "icyp.make_mesh_from_envelopes"
	bl_label = "(WIP)basic mesh for vrm"
	bl_description = "Create mesh along with a simple setup for VRM export"
	bl_options = {'REGISTER', 'UNDO'}

	@classmethod
	def poll(self,context):
		return True
	def execute(self, context):
		self.build_mesh(context)
		return {"FINISHED"}

	resolution: bpy.props.IntProperty(default=5, min=2)
	use_selected_bones: bpy.props.BoolProperty(default=False)
	may_vrm_humanoid : bpy.props.BoolProperty(default=True)
	with_auto_weight : bpy.props.BoolProperty(default=True)

	def build_mesh(self,context):
		if bpy.context.active_object.type !='ARMATURE':
			return
		bpy.ops.object.mode_set(mode='OBJECT')
		armature = bpy.context.active_object
		mball = bpy.data.metaballs.new(f"{armature.name}_mball")
		mball.threshold = 0.001
		for bone in armature.data.bones:
			if self.use_selected_bones and bone.select == False:
				continue
			if "title" in armature and self.may_vrm_humanoid: # = is VRM humanoid
				if bone.get("humanBone") in ("leftEye","rightEye","Hips"): 
					continue
				if bone.name == "root":
					continue
			hpos = bone.head_local
			hrad = bone.head_radius
			tpos = bone.tail_local
			trad = bone.tail_radius
			for i in range(self.resolution):
				loc = hpos + ((tpos - hpos) / (self.resolution-1)) * i
				rad = hrad + ((trad - hrad) / (self.resolution-1)) * i
				elem = mball.elements.new()
				elem.co = loc
				elem.radius = rad
			if min([hrad,trad]) < mball.resolution:
				mball.resolution = min([hrad,trad])
		mobj = bpy.data.objects.new(f"{armature.name}_mesh",mball)
		mobj.location = armature.location
		mobj.rotation_quaternion = armature.rotation_quaternion
		mobj.scale = armature.scale
		obj_name = mobj.name
		bpy.ops.object.select_all(action="DESELECT")
		bpy.ops.object.mode_set(mode='OBJECT')
		context.scene.collection.objects.link(mobj)
		context.view_layer.objects.active = mobj
		mobj.select_set(True)
		bpy.ops.object.convert(target='MESH')

		obj = context.view_layer.objects.active
		context.view_layer.objects.active = armature
		obj.select_set(True)
		if self.with_auto_weight:
			bpy.ops.object.parent_set(type='ARMATURE_AUTO')
			obj.select_set(True)
			context.view_layer.objects.active = obj
			bpy.ops.object.vertex_group_limit_total(limit=4)
		armature.select_set(False)

		context.view_layer.objects.active = obj
		bpy.ops.object.mode_set(mode='EDIT')
		bpy.ops.mesh.select_all(action='SELECT')
		bpy.ops.mesh.quads_convert_to_tris(quad_method='BEAUTY', ngon_method='BEAUTY')
		bpy.ops.uv.smart_project()

		
		def material_init(mat):
			mat.use_nodes = True
			for node in mat.node_tree.nodes:
				if node.type != "OUTPUT_MATERIAL":
					mat.node_tree.nodes.remove(node)
			return
		
		def node_group_import(shader_node_group_name):
			if shader_node_group_name not in bpy.data.node_groups:
				filedir = os.path.join(os.path.dirname(os.path.dirname(__file__)),"resources","material_node_groups.blend","NodeTree")
				filepath = os.path.join(filedir,shader_node_group_name)
				bpy.ops.wm.append(filepath=filepath,filename=shader_node_group_name,directory=filedir)
			return
		def node_group_create(material,shader_node_group_name):
			node_group = material.node_tree.nodes.new("ShaderNodeGroup")
			node_group.node_tree = bpy.data.node_groups[shader_node_group_name]
			return node_group

		shader_node_group_name = "MToon_unversioned"
		node_group_import(shader_node_group_name)
		b_mat = bpy.data.materials.new(f"{armature.name}_mesh_mat")
		material_init(b_mat)
		sg = node_group_create(b_mat,shader_node_group_name)
		b_mat.node_tree.links.new(b_mat.node_tree.nodes["Material Output"].inputs['Surface'], sg.outputs["Emission"])
		obj.data.materials.append(b_mat)
	
		bpy.ops.object.mode_set(mode='OBJECT')
		armature.select_set(True)


		return 