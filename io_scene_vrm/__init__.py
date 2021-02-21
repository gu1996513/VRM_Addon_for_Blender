"""
Copyright (c) 2018 iCyP
Released under the MIT license
https://opensource.org/licenses/mit-license.php

"""

import os
import traceback
from typing import Any, Set, cast

import bpy
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import vrm_types
from .importer import blend_model, vrm_load
from .misc import (
    detail_mesh_maker,
    glb_factory,
    glsl_drawer,
    make_armature,
    mesh_from_bone_envelopes,
    version,
    vrm_helper,
)
from .misc.glsl_drawer import GlslDrawObj
from .misc.preferences import get_preferences

addon_package_name = ".".join(__name__.split(".")[:-1])


class VrmAddonPreferences(bpy.types.AddonPreferences):  # type: ignore[misc]
    bl_idname = addon_package_name

    export_invisibles: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Export invisible objects",  # noqa: F722
        default=False,
    )
    export_only_selections: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Export only selections",  # noqa: F722
        default=False,
    )

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.prop(self, "export_invisibles")
        layout.prop(self, "export_only_selections")


class LicenseConfirmation(bpy.types.PropertyGroup):  # type: ignore[misc]
    message: bpy.props.StringProperty()  # type: ignore[valid-type]
    url: bpy.props.StringProperty()  # type: ignore[valid-type]
    json_key: bpy.props.StringProperty()  # type: ignore[valid-type]


class ImportVRM(bpy.types.Operator, ImportHelper):  # type: ignore[misc]
    bl_idname = "import_scene.vrm"
    bl_label = "Import VRM"
    bl_description = "Import VRM"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".vrm"
    filter_glob: bpy.props.StringProperty(  # type: ignore[valid-type]
        default="*.vrm", options={"HIDDEN"}  # noqa: F722,F821
    )

    extract_textures_into_folder: bpy.props.BoolProperty(  # type: ignore[valid-type]
        default=False, name="Extract texture images into the folder"  # noqa: F722
    )
    make_new_texture_folder: bpy.props.BoolProperty(  # type: ignore[valid-type]
        default=True,
        name="Don't overwrite existing texture folder (limit:100,000)",  # noqa: F722
    )

    def execute(self, context: bpy.types.Context) -> Set[str]:
        license_error = None
        try:
            return create_blend_model(
                self,
                context,
                vrm_load.read_vrm(
                    self.filepath,
                    self.extract_textures_into_folder,
                    self.make_new_texture_folder,
                    license_check=True,
                ),
            )
        except vrm_load.LicenseConfirmationRequired as e:
            license_error = e  # Prevent traceback dump on another exception

        print(license_error.description())

        execution_context = "INVOKE_DEFAULT"
        import_anyway = False
        if os.environ.get("BLENDER_VRM_AUTOMATIC_LICENSE_CONFIRMATION") == "true":
            execution_context = "EXEC_DEFAULT"
            import_anyway = True

        return cast(
            Set[str],
            bpy.ops.wm.vrm_license_warning(
                execution_context,
                import_anyway=import_anyway,
                license_confirmations=license_error.license_confirmations(),
                filepath=self.filepath,
                extract_textures_into_folder=self.extract_textures_into_folder,
                make_new_texture_folder=self.make_new_texture_folder,
            ),
        )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> Set[str]:
        if bpy.app.version < (2, 83):
            return cast(
                Set[str],
                bpy.ops.wm.import_blender_version_warning(
                    "INVOKE_DEFAULT",
                ),
            )
        if "gltf" not in dir(bpy.ops.import_scene):
            return cast(
                Set[str],
                bpy.ops.wm.gltf2_addon_disabled_warning(
                    "INVOKE_DEFAULT",
                ),
            )
        return cast(Set[str], ImportHelper.invoke(self, context, event))


def create_blend_model(
    addon: Any, context: bpy.types.Context, vrm_pydata: vrm_types.VrmPydata
) -> Set[str]:
    has_ui_localization = bpy.app.version < (2, 83)
    ui_localization = False
    if has_ui_localization:
        ui_localization = bpy.context.preferences.view.use_international_fonts
    try:
        blend_model.BlendModel(
            context,
            vrm_pydata,
            addon.extract_textures_into_folder,
            addon.make_new_texture_folder,
        )
    finally:
        if has_ui_localization and ui_localization:
            bpy.context.preferences.view.use_international_fonts = ui_localization

    return {"FINISHED"}


def menu_import(
    import_op: bpy.types.Operator, context: bpy.types.Context
) -> None:  # Same as test/blender_io.py for now
    import_op.layout.operator(ImportVRM.bl_idname, text="VRM (.vrm)")


def export_vrm_update_addon_preferences(
    export_op: bpy.types.Operator, context: bpy.types.Context
) -> None:
    preferences = get_preferences(context)
    if not preferences:
        return
    if bool(preferences.export_invisibles) != bool(export_op.export_invisibles):
        preferences.export_invisibles = export_op.export_invisibles
    if bool(preferences.export_only_selections) != bool(
        export_op.export_only_selections
    ):
        preferences.export_only_selections = export_op.export_only_selections


class ExportVRM(bpy.types.Operator, ExportHelper):  # type: ignore[misc]
    bl_idname = "export_scene.vrm"
    bl_label = "Export VRM"
    bl_description = "Export VRM"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".vrm"
    filter_glob: bpy.props.StringProperty(  # type: ignore[valid-type]
        default="*.vrm", options={"HIDDEN"}  # noqa: F722,F821
    )

    # vrm_version : bpy.props.EnumProperty(name="VRM version" ,items=(("0.0","0.0",""),("1.0","1.0","")))
    export_invisibles: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Export invisible objects",  # noqa: F722
        update=export_vrm_update_addon_preferences,
    )
    export_only_selections: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Export only selections",  # noqa: F722
        update=export_vrm_update_addon_preferences,
    )

    errors: bpy.props.CollectionProperty(type=vrm_helper.VrmValidationError)  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context) -> Set[str]:
        if not self.filepath:
            return {"CANCELLED"}
        filepath: str = self.filepath

        try:
            glb_obj = glb_factory.GlbObj(
                bool(self.export_invisibles), bool(self.export_only_selections)
            )
        except glb_factory.GlbObj.ValidationError:
            return {"CANCELLED"}
        # vrm_bin =  glb_obj().convert_bpy2glb(self.vrm_version)
        vrm_bin = glb_obj.convert_bpy2glb("0.0")
        if vrm_bin is None:
            return {"CANCELLED"}
        with open(filepath, "wb") as f:
            f.write(vrm_bin)
        return {"FINISHED"}

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> Set[str]:
        if bpy.app.version < (2, 83):
            return cast(
                Set[str],
                bpy.ops.wm.export_blender_version_warning(
                    "INVOKE_DEFAULT",
                ),
            )
        preferences = get_preferences(context)
        if preferences:
            self.export_invisibles = bool(preferences.export_invisibles)
            self.export_only_selections = bool(preferences.export_only_selections)
        return cast(Set[str], ExportHelper.invoke(self, context, event))

    def draw(self, context: bpy.types.Context) -> None:
        pass  # Is needed to get panels available


class VRM_IMPORTER_PT_export_error_messages(bpy.types.Panel):  # type: ignore[misc] # noqa: N801
    bl_space_type = "FILE_BROWSER"
    bl_region_type = "TOOL_PROPS"
    bl_parent_id = "FILE_PT_operator"
    bl_label = ""
    bl_options = {"HIDE_HEADER"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return (
            str(context.space_data.active_operator.bl_idname) == "EXPORT_SCENE_OT_vrm"
        )

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False  # No animation.

        operator = context.space_data.active_operator

        layout.prop(operator, "export_invisibles")
        layout.prop(operator, "export_only_selections")

        vrm_helper.WM_OT_vrmValidator.detect_errors_and_warnings(
            context, operator.errors, False, layout
        )


def menu_export(export_op: bpy.types.Operator, context: bpy.types.Context) -> None:
    export_op.layout.operator(ExportVRM.bl_idname, text="VRM (.vrm)")


def add_armature(
    add_armature_op: bpy.types.Operator, context: bpy.types.Context
) -> None:
    add_armature_op.layout.operator(
        make_armature.ICYP_OT_MAKE_ARMATURE.bl_idname,
        text="VRM Humanoid",
        icon="OUTLINER_OB_ARMATURE",
    )


def make_mesh(make_mesh_op: bpy.types.Operator, context: bpy.types.Context) -> None:
    make_mesh_op.layout.separator()
    make_mesh_op.layout.operator(
        mesh_from_bone_envelopes.ICYP_OT_MAKE_MESH_FROM_BONE_ENVELOPES.bl_idname,
        text="Mesh from selected armature",
        icon="PLUGIN",
    )
    make_mesh_op.layout.operator(
        detail_mesh_maker.ICYP_OT_DETAIL_MESH_MAKER.bl_idname,
        text="(WIP)Face mesh from selected armature and bound mesh",
        icon="PLUGIN",
    )


class VRM_IMPORTER_PT_controller(bpy.types.Panel):  # type: ignore[misc] # noqa: N801
    bl_idname = "ICYP_PT_ui_controller"
    bl_label = "VRM Helper"
    # どこに置くかの定義
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "VRM"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return True

    def draw(self, context: bpy.types.Context) -> None:
        # region helper
        def armature_ui() -> None:
            self.layout.separator()
            armature_box = self.layout.row(align=False).box()
            armature_box.label(text="Armature Help")
            armature_box.operator(vrm_helper.Add_VRM_extensions_to_armature.bl_idname)
            self.layout.separator()

            requires_box = armature_box.box()
            requires_row = requires_box.row()
            requires_row.label(text="VRM Required Bones")
            for req in vrm_types.HumanBones.requires:
                if req in context.active_object.data:
                    requires_box.prop_search(
                        context.active_object.data,
                        f'["{req}"]',
                        context.active_object.data,
                        "bones",
                        text=req,
                    )
                else:
                    requires_box.operator(
                        vrm_helper.Add_VRM_require_humanbone_custom_property.bl_idname,
                        text=f"Add {req} property",
                    )
            defines_box = armature_box.box()
            defines_box.label(text="VRM Optional Bones")
            for defs in vrm_types.HumanBones.defines:
                if defs in context.active_object.data:
                    defines_box.prop_search(
                        context.active_object.data,
                        f'["{defs}"]',
                        context.active_object.data,
                        "bones",
                        text=defs,
                    )
                else:
                    defines_box.operator(
                        vrm_helper.Add_VRM_defined_humanbone_custom_property.bl_idname,
                        text=f"Add {defs} property",
                    )

            armature_box.label(icon="ERROR", text="EXPERIMENTAL!!!")
            armature_box.operator(vrm_helper.Bones_rename.bl_idname)

        # endregion helper

        # region draw_main
        if (
            context.mode != "POSE"
            or context.active_object is None
            or context.active_object.type != "ARMATURE"
        ):
            self.layout.label(text="If you select armature in object mode")
            self.layout.label(text="armature renamer is shown")
        if context.mode != "EDIT_MESH":
            self.layout.label(text="If you in MESH EDIT")
            self.layout.label(text="symmetry button is shown")
            self.layout.label(text="*Symmetry is in default blender function")
        if context.mode == "OBJECT":
            object_mode_box = self.layout.box()
            preferences = get_preferences(context)
            if preferences:
                object_mode_box.prop(
                    preferences,
                    "export_invisibles",
                    text=vrm_helper.lang_support(
                        "Export invisible objects", "非表示オブジェクトを含める"
                    ),
                )
                object_mode_box.prop(
                    preferences,
                    "export_only_selections",
                    text=vrm_helper.lang_support(
                        "Export only selections", "選択されたオブジェクトのみ"
                    ),
                )
            vrm_validator_prop = object_mode_box.operator(
                vrm_helper.WM_OT_vrmValidator.bl_idname,
                text=vrm_helper.lang_support("Validate VRM model", "VRMモデルのチェック"),
            )
            vrm_validator_prop.show_successful_message = True
            # vrm_validator_prop.errors = []  # これはできない
            object_mode_box.label(text="MToon preview")
            if [obj for obj in bpy.data.objects if obj.type == "LIGHT"]:
                object_mode_box.operator(glsl_drawer.ICYP_OT_Draw_Model.bl_idname)
            else:
                object_mode_box.box().label(
                    icon="INFO",
                    text=vrm_helper.lang_support("A light is required", "ライトが必要です"),
                )
            if GlslDrawObj.draw_objs:
                object_mode_box.operator(
                    glsl_drawer.ICYP_OT_Remove_Draw_Model.bl_idname
                )
            if context.active_object is not None:
                if context.active_object.type == "ARMATURE":
                    armature_ui()
                if context.active_object.type == "MESH":
                    self.layout.label(icon="ERROR", text="EXPERIMENTAL!!!")
                    self.layout.operator(
                        vrm_helper.Vroid2VRC_lipsync_from_json_recipe.bl_idname
                    )

        if context.mode == "EDIT_MESH":
            self.layout.operator(bpy.ops.mesh.symmetry_snap.idname_py())

        if (
            context.mode == "POSE"
            and context.active_object is not None  # Noneになることは有り得ないかもしれない
            and context.active_object.type == "ARMATURE"
        ):
            armature_ui()
        # endregion draw_main


class WM_OT_gltf2AddonDisabledWarning(bpy.types.Operator):  # type: ignore[misc] # noqa: N801
    bl_label = "glTF 2.0 Addon is disabled"
    bl_idname = "wm.gltf2_addon_disabled_warning"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> Set[str]:
        return {"FINISHED"}

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> Set[str]:
        return cast(
            Set[str], context.window_manager.invoke_props_dialog(self, width=500)
        )

    def draw(self, context: bpy.types.Context) -> None:
        self.layout.label(
            icon="ERROR",
            text='Official add-on "glTF 2.0 format" is required. Please enable it.',
        )


class WM_OT_importBlenderVersionWarning(bpy.types.Operator):  # type: ignore[misc] # noqa: N801
    bl_label = "Please upgrade Blender to version 2.83 or later as far as possible."
    bl_idname = "wm.import_blender_version_warning"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> Set[str]:
        return cast(Set[str], bpy.ops.import_scene.vrm("INVOKE_DEFAULT"))

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> Set[str]:
        return cast(
            Set[str], context.window_manager.invoke_props_dialog(self, width=600)
        )

    def draw(self, context: bpy.types.Context) -> None:
        self.layout.label(
            icon="ERROR",
            text="Importing and exporting VRM on Blender version less than 2.83 is very unstable.",
        )


class WM_OT_exportBlenderVersionWarning(bpy.types.Operator):  # type: ignore[misc] # noqa: N801
    bl_label = "Please upgrade Blender to version 2.83 or later as far as possible."
    bl_idname = "wm.export_blender_version_warning"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> Set[str]:
        return cast(Set[str], bpy.ops.export_scene.vrm("INVOKE_DEFAULT"))

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> Set[str]:
        return cast(
            Set[str], context.window_manager.invoke_props_dialog(self, width=600)
        )

    def draw(self, context: bpy.types.Context) -> None:
        self.layout.label(
            icon="ERROR",
            text="Importing and exporting VRM on Blender version less than 2.83 is very unstable.",
        )


class WM_OT_licenseConfirmation(bpy.types.Operator):  # type: ignore[misc] # noqa: N801
    bl_label = "License confirmation"
    bl_idname = "wm.vrm_license_warning"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty()  # type: ignore[valid-type]

    license_confirmations: bpy.props.CollectionProperty(type=LicenseConfirmation)  # type: ignore[valid-type]
    import_anyway: bpy.props.BoolProperty()  # type: ignore[valid-type]

    extract_textures_into_folder: bpy.props.BoolProperty()  # type: ignore[valid-type]
    make_new_texture_folder: bpy.props.BoolProperty()  # type: ignore[valid-type]

    def execute(self, context: bpy.types.Context) -> Set[str]:
        if not self.import_anyway:
            return {"CANCELLED"}
        return create_blend_model(
            self,
            context,
            vrm_load.read_vrm(
                self.filepath,
                self.extract_textures_into_folder,
                self.make_new_texture_folder,
                license_check=False,
            ),
        )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> Set[str]:
        return cast(
            Set[str], context.window_manager.invoke_props_dialog(self, width=600)
        )

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.label(text=self.filepath)
        for license_confirmation in self.license_confirmations:
            for line in license_confirmation.message.split("\n"):
                layout.label(text=line)
            if license_confirmation.json_key:
                layout.label(
                    text=vrm_helper.lang_support(
                        "For more information please check following URL.",
                        "詳しくは下記のURLを確認してください。",
                    )
                )
                layout.prop(
                    license_confirmation,
                    "url",
                    text=license_confirmation.json_key,
                    translate=False,
                )
        layout.prop(
            self,
            "import_anyway",
            text=vrm_helper.lang_support("Import anyway", "インポートします"),
        )


if persistent:  # for fake-bpy-modules

    @persistent  # type: ignore[misc]
    def add_shaders(self: Any) -> None:
        filedir = os.path.join(
            os.path.dirname(__file__), "resources", "material_node_groups.blend"
        )
        with bpy.data.libraries.load(filedir, link=False) as (data_from, data_to):
            for nt in data_from.node_groups:
                if nt not in bpy.data.node_groups:
                    data_to.node_groups.append(nt)


classes = [
    VrmAddonPreferences,
    LicenseConfirmation,
    WM_OT_licenseConfirmation,
    WM_OT_gltf2AddonDisabledWarning,
    WM_OT_importBlenderVersionWarning,
    WM_OT_exportBlenderVersionWarning,
    vrm_helper.Bones_rename,
    vrm_helper.Add_VRM_extensions_to_armature,
    vrm_helper.Add_VRM_require_humanbone_custom_property,
    vrm_helper.Add_VRM_defined_humanbone_custom_property,
    vrm_helper.Vroid2VRC_lipsync_from_json_recipe,
    vrm_helper.VrmValidationError,
    vrm_helper.WM_OT_vrmValidator,
    ImportVRM,
    ExportVRM,
    VRM_IMPORTER_PT_export_error_messages,
    VRM_IMPORTER_PT_controller,
    make_armature.ICYP_OT_MAKE_ARMATURE,
    glsl_drawer.ICYP_OT_Draw_Model,
    glsl_drawer.ICYP_OT_Remove_Draw_Model,
    # detail_mesh_maker.ICYP_OT_DETAIL_MESH_MAKER,
    # blend_model.ICYP_OT_select_helper,
    # mesh_from_bone_envelopes.ICYP_OT_MAKE_MESH_FROM_BONE_ENVELOPES
]

translation_dictionary = {
    "ja_JP": {
        ("*", "Export invisible objects"): "非表示のオブジェクトも含める",
        ("*", "Export only selections"): "選択されたオブジェクトのみ",
        ("*", "MToon preview"): "MToonのプレビュー",
        ("*", "No error. Ready for export VRM"): "エラーはありませんでした。VRMのエクスポートをすることができます",
        ("*", "VRM Export"): "VRMエクスポート",
        ("*", "Validate VRM model"): "VRMモデルのチェック",
        ("*", "Extract texture images into the folder"): "テクスチャ画像をフォルダに展開",
        (
            "*",
            'Official add-on "glTF 2.0 format" is required. Please enable it.',
        ): "公式アドオン「gltf 2.0 format」が必要です。有効化してください。",
        (
            "*",
            "Please upgrade Blender to version 2.83 or later as far as possible.",
        ): "可能な限り、Blenderのバージョンを2.83以上にアップデートして下さい。",
        (
            "*",
            "Importing and exporting VRM on Blender version less than 2.83 is very unstable.",
        ): "Blenderのバージョンが2.83未満だと、VRMのインポートやエクスポートが非常に不安定です。",
    }
}


# アドオン有効化時の処理
def register(init_version: Any) -> None:
    # Sanity check
    if init_version != version.version():
        raise Exception(f"Version mismatch: {init_version} != {version.version()}")

    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)
    bpy.types.VIEW3D_MT_armature_add.append(add_armature)
    # bpy.types.VIEW3D_MT_mesh_add.append(make_mesh)
    bpy.app.handlers.load_post.append(add_shaders)
    bpy.app.translations.register(addon_package_name, translation_dictionary)


# アドオン無効化時の処理
def unregister() -> None:
    bpy.app.translations.unregister(addon_package_name)
    bpy.app.handlers.load_post.remove(add_shaders)
    bpy.types.VIEW3D_MT_armature_add.remove(add_armature)
    # bpy.types.VIEW3D_MT_mesh_add.remove(make_mesh)
    bpy.types.TOPBAR_MT_file_import.remove(menu_export)
    bpy.types.TOPBAR_MT_file_export.remove(menu_import)
    errors = []
    for cls in classes:
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            errors.append(
                traceback.format_exc() + f"\nbpy.utils.unregister_class({cls}):"
            )
    if errors:
        raise RuntimeError("\n".join(errors))
