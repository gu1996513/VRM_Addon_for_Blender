"""
Copyright (c) 2018 iCyP
Released under the MIT license
https://opensource.org/licenses/mit-license.php

"""

import contextlib
import json
import math
import re
import sys
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import ParseResult, parse_qsl, urlparse

import numpy

from .. import vrm_types
from ..gl_constants import GlConstants
from ..misc import vrm_helper
from ..vrm_types import nested_json_value_getter as json_get
from . import vrm2pydata_factory
from .binary_reader import BinaryReader


class LicenseConfirmationRequiredProp:
    def __init__(
        self,
        url: Optional[str],
        json_key: Optional[str],
        message_en: str,
        message_ja: str,
    ) -> None:
        self.url = url
        self.json_key = json_key
        self.message = vrm_helper.lang_support(message_en, message_ja)

    def description(self) -> str:
        return f"""class=LicenseConfirmationRequired
url={self.url}
json_key={self.json_key}
message={self.message}
"""


class LicenseConfirmationRequired(Exception):
    def __init__(self, props: List[LicenseConfirmationRequiredProp]) -> None:
        self.props = props
        super().__init__(self.description())

    def description(self) -> str:
        return "\n".join([prop.description() for prop in self.props])

    def license_confirmations(self) -> List[Dict[str, str]]:
        return [
            {
                "name": "LicenseConfirmation" + str(index),
                "url": prop.url or "",
                "json_key": prop.json_key or "",
                "message": prop.message or "",
            }
            for index, prop in enumerate(self.props)
        ]


def parse_glb(data: bytes) -> Tuple[Dict[str, Any], bytes]:
    reader = BinaryReader(data)
    magic = reader.read_str(4)
    if magic != "glTF":
        raise Exception("glTF header signature not found: #{}".format(magic))

    version = reader.read_as_data_type(GlConstants.UNSIGNED_INT)
    if version != 2:
        raise Exception(
            "version #{} found. This plugin only supports version 2".format(version)
        )

    size = reader.read_as_data_type(GlConstants.UNSIGNED_INT)
    size -= 12

    json_str: Optional[str] = None
    body: Optional[bytes] = None
    while size > 0:
        # print(size)

        if json_str is not None and body is not None:
            raise Exception(
                "This VRM has multiple chunks, this plugin reads one chunk only."
            )

        chunk_size = reader.read_unsigned_int()
        size -= 4

        chunk_type = reader.read_str(4)
        size -= 4

        chunk_data = reader.read_binary(chunk_size)
        size -= chunk_size

        if chunk_type == "BIN\x00":
            body = chunk_data
            continue
        if chunk_type == "JSON":
            json_str = chunk_data.decode("utf-8")  # blenderのpythonverが古く自前decode要す
            continue

        raise Exception("unknown chunk_type: {}".format(chunk_type))

    if not json_str:
        raise Exception("failed to read json chunk")

    json_obj = json.loads(json_str, object_pairs_hook=OrderedDict)
    if not isinstance(json_obj, dict):
        raise Exception("VRM has invalid json: " + str(json_obj))
    return json_obj, body if body else bytes()


# あくまでvrm(の特にバイナリ)をpythonデータ化するだけで、blender型に変形はここではしない
def read_vrm(
    model_path: str,
    extract_textures_into_folder: bool,
    make_new_texture_folder: bool,
    license_check: bool,
) -> vrm_types.VrmPydata:
    # datachunkは普通一つしかない
    with open(model_path, "rb") as f:
        json_dict, body_binary = parse_glb(f.read())
        vrm_pydata = vrm_types.VrmPydata(model_path, json_dict)

    # KHR_DRACO_MESH_COMPRESSION は対応してない場合落とさないといけないらしい。どのみち壊れたデータになるからね。
    if (
        "extensionsRequired" in vrm_pydata.json
        and "KHR_DRACO_MESH_COMPRESSION" in vrm_pydata.json["extensionsRequired"]
    ):
        raise Exception(
            "This VRM uses Draco compression. Unable to decompress. Draco圧縮されたVRMは未対応です"
        )

    if license_check:
        validate_license(vrm_pydata)

    material_read(vrm_pydata)
    return vrm_pydata


def validate_license_url(
    url_str: str, json_key: str, props: List[LicenseConfirmationRequiredProp]
) -> None:
    if not url_str:
        return
    url = None
    with contextlib.suppress(ValueError):
        url = urlparse(url_str)
    if url:
        query_dict = dict(parse_qsl(url.query))
        if validate_vroid_hub_license_url(
            url, query_dict, json_key, props
        ) or validate_uni_virtual_license_url(url, query_dict, json_key, props):
            return
    props.append(
        LicenseConfirmationRequiredProp(
            url_str,
            json_key,
            "Is this VRM allowed to edited? Please check its copyright license.",
            "独自のライセンスが記載されています。",
        )
    )


def validate_vroid_hub_license_url(
    url: ParseResult,
    query_dict: Dict[str, str],
    json_key: str,
    props: List[LicenseConfirmationRequiredProp],
) -> bool:
    # https://hub.vroid.com/en/license?allowed_to_use_user=everyone&characterization_allowed_user=everyone&corporate_commercial_use=allow&credit=unnecessary&modification=allow&personal_commercial_use=profit&redistribution=allow&sexual_expression=allow&version=1&violent_expression=allow
    if url.hostname != "hub.vroid.com" or not url.path.endswith("/license"):
        return False
    if query_dict.get("modification") == "disallow":
        props.append(
            LicenseConfirmationRequiredProp(
                url.geturl(),
                json_key,
                'This VRM is licensed by VRoid Hub License "Alterations: No".',
                "このVRMにはVRoid Hubの「改変: NG」ライセンスが設定されています。",
            )
        )
    return True


def validate_uni_virtual_license_url(
    url: ParseResult,
    query_dict: Dict[str, str],
    json_key: str,
    props: List[LicenseConfirmationRequiredProp],
) -> bool:
    # https://uv-license.com/en/license?utf8=%E2%9C%93&pcu=true
    if url.hostname != "uv-license.com" or not url.path.endswith("/license"):
        return False
    if query_dict.get("remarks") == "true":
        props.append(
            LicenseConfirmationRequiredProp(
                url.geturl(),
                json_key,
                'This VRM is licensed by UV License with "Remarks".',
                "このVRMには特記事項(Remarks)付きのUVライセンスが設定されています。",
            )
        )
    return True


def validate_license(vrm_pydata: vrm_types.VrmPydata) -> None:
    confirmations: List[LicenseConfirmationRequiredProp] = []

    # 既知の改変不可ライセンスを撥ねる
    # CC_NDなど
    license_name = str(
        json_get(vrm_pydata.json, ["extensions", "VRM", "meta", "licenseName"], "")
    )
    if re.match("CC(.*)ND(.*)", license_name):
        confirmations.append(
            LicenseConfirmationRequiredProp(
                None,
                None,
                'The VRM is licensed by "{license_name}".\nNo derivative works are allowed.',
                f"指定されたVRMは改変不可ライセンス「{license_name}」が設定されています。\n改変することはできません。",
            )
        )

    validate_license_url(
        str(
            json_get(
                vrm_pydata.json, ["extensions", "VRM", "meta", "otherPermissionUrl"], ""
            )
        ),
        "otherPermissionUrl",
        confirmations,
    )

    if license_name == "Other":
        other_license_url_str = str(
            json_get(
                vrm_pydata.json, ["extensions", "VRM", "meta", "otherLicenseUrl"], ""
            )
        )
        if not other_license_url_str:
            confirmations.append(
                LicenseConfirmationRequiredProp(
                    None,
                    None,
                    'The VRM selects "Other" license but no license url is found.',
                    "このVRMには「Other」ライセンスが指定されていますが、URLが設定されていません。",
                )
            )
        else:
            validate_license_url(
                other_license_url_str, "otherLicenseUrl", confirmations
            )

    if confirmations:
        raise LicenseConfirmationRequired(confirmations)


def remove_unsafe_path_chars(filename: str) -> str:
    unsafe_chars = {
        0: "\x00",
        1: "\x01",
        2: "\x02",
        3: "\x03",
        4: "\x04",
        5: "\x05",
        6: "\x06",
        7: "\x07",
        8: "\x08",
        9: "\t",
        10: "\n",
        11: "\x0b",
        12: "\x0c",
        13: "\r",
        14: "\x0e",
        15: "\x0f",
        16: "\x10",
        17: "\x11",
        18: "\x12",
        19: "\x13",
        20: "\x14",
        21: "\x15",
        22: "\x16",
        23: "\x17",
        24: "\x18",
        25: "\x19",
        26: "\x1a",
        27: "\x1b",
        28: "\x1c",
        29: "\x1d",
        30: "\x1e",
        31: "\x1f",
        34: '"',
        42: "*",
        47: "/",
        58: ":",
        60: "<",
        62: ">",
        63: "?",
        92: "\\",
        124: "|",
    }  # 32:space #33:!
    remove_table = str.maketrans(
        "", "", "".join([chr(charnum) for charnum in unsafe_chars])
    )
    safe_filename = filename.translate(remove_table)
    return safe_filename


#  "accessorの順に" データを読み込んでリストにしたものを返す
def decode_bin(json_data: Dict[str, Any], binary: bytes) -> List[Any]:
    br = BinaryReader(binary)
    # This list indexed by accessor index
    decoded_binary: List[Any] = []
    buffer_views = json_data["bufferViews"]
    accessors = json_data["accessors"]
    type_num_dict = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
    for accessor_index, accessor in enumerate(accessors):
        type_num = type_num_dict[accessor["type"]]
        if "bufferView" not in accessor:
            print(
                f"WARNING: accessors[{accessor_index}] doesn't have bufferView that is not implemented yet"
            )
            decoded_binary.append([])
            continue
        br.set_pos(buffer_views[accessor["bufferView"]]["byteOffset"])
        data_list = []
        for _ in range(accessor["count"]):
            if type_num == 1:
                data = br.read_as_data_type(accessor["componentType"])
            else:
                data = []  # type: ignore[assignment]
                for _ in range(type_num):
                    data.append(br.read_as_data_type(accessor["componentType"]))  # type: ignore[union-attr]
            data_list.append(data)
        decoded_binary.append(data_list)

    return decoded_binary


def mesh_read(vrm_pydata: vrm_types.VrmPydata) -> None:
    # メッシュをパースする
    for n, mesh in enumerate(vrm_pydata.json.get("meshes", [])):
        primitives = []
        for j, primitive in enumerate(mesh.get("primitives", [])):
            vrm_mesh = vrm_types.Mesh(object_id=n)
            if j == 0:  # mesh annotationとの兼ね合い
                vrm_mesh.name = mesh["name"]
            else:
                vrm_mesh.name = mesh["name"] + str(j)

            # region 頂点index
            if primitive.get("mode", 4) != GlConstants.TRIANGLES:
                # TODO その他メッシュタイプ対応
                raise Exception(
                    "Unsupported polygon type(:{}) Exception".format(primitive["mode"])
                )
            face_indices = vrm_pydata.decoded_binary[primitive["indices"]]
            # 3要素ずつに変換しておく(GlConstants.TRIANGLES前提なので)
            # ATTENTION これだけndarray
            vrm_mesh.face_indices = numpy.reshape(face_indices, (-1, 3))
            # endregion 頂点index

            # ここから頂点属性
            vertex_attributes = primitive.get("attributes", {})
            # 頂点属性は実装によっては存在しない属性(例えばJOINTSやWEIGHTSがなかったりもする)もあるし、UVや頂点カラー0->Nで増やせる(スキニングは1要素(ボーン4本)限定
            for attr in vertex_attributes.keys():
                vrm_mesh.__setattr__(
                    attr, vrm_pydata.decoded_binary[vertex_attributes[attr]]
                )

            # region TEXCOORD_FIX [ 古いUniVRM誤り: uv.y = -uv.y ->修復 uv.y = 1 - ( -uv.y ) => uv.y=1+uv.y]
            legacy_uv_flag = False  # f***
            gen = str(json_get(vrm_pydata.json, ["assets", "generator"], ""))
            if re.match("UniGLTF", gen):
                with contextlib.suppress(ValueError):
                    if float("".join(gen[-4:])) < 1.16:
                        legacy_uv_flag = True

            uv_count = 0
            while True:
                texcoord_name = "TEXCOORD_{}".format(uv_count)
                if hasattr(vrm_mesh, texcoord_name):
                    texcoord = getattr(vrm_mesh, texcoord_name)
                    if legacy_uv_flag:
                        for uv in texcoord:
                            uv[1] = 1 + uv[1]
                    uv_count += 1
                else:
                    break
            # blenderとは上下反対のuv,それはblenderに書き込むときに直す
            # endregion TEXCOORD_FIX

            # meshに当てられるマテリアルの場所を記録
            vrm_mesh.material_index = primitive["material"]

            # 変換時のキャッシュ対応のためのデータ
            vrm_mesh.POSITION_accessor = primitive.get("attributes", {}).get("POSITION")

            # ここからモーフターゲット vrmのtargetは相対位置 normalは無視する
            if "targets" in primitive:
                morph_target_point_list_and_accessor_index_dict = OrderedDict()
                for i, morph_target in enumerate(primitive["targets"]):
                    pos_array = vrm_pydata.decoded_binary[morph_target["POSITION"]]
                    if "extra" in morph_target:  # for old AliciaSolid
                        # accessorのindexを持つのは変換時のキャッシュ対応のため
                        morph_name = str(primitive["targets"][i]["extra"]["name"])
                    else:
                        morph_name = str(primitive["extras"]["targetNames"][i])
                        # 同上
                    morph_target_point_list_and_accessor_index_dict[morph_name] = [
                        pos_array,
                        primitive["targets"][i]["POSITION"],
                    ]
                vrm_mesh.morph_target_point_list_and_accessor_index_dict = (
                    morph_target_point_list_and_accessor_index_dict
                )
            primitives.append(vrm_mesh)
        vrm_pydata.meshes.append(primitives)

    # ここからマテリアル


def material_read(vrm_pydata: vrm_types.VrmPydata) -> None:
    json_materials = vrm_pydata.json.get("materials", [])
    vrm_extension_material_properties = json_get(
        vrm_pydata.json,
        ["extensions", "VRM", "materialProperties"],
        default=[{"shader": "VRM_USE_GLTFSHADER"}] * len(json_materials),
    )
    if not isinstance(vrm_extension_material_properties, list):
        return

    for mat, ext_mat in zip(json_materials, vrm_extension_material_properties):
        material = vrm2pydata_factory.material(mat, ext_mat)
        if material is not None:
            vrm_pydata.materials.append(material)

    # skinをパース ->バイナリの中身はskinning実装の横着用
    # skinのjointsの(nodesの)indexをvertsのjoints_0は指定してる
    # inverseBindMatrices: 単にスキニングするときの逆行列。読み込み不要なのでしない(自前計算もできる、めんどいけど)
    # ついでに[i][3]ではなく、[3][i]にマイナスx,y,zが入っている。 ここで詰まった。(出力時に)
    # joints:JOINTS_0の指定node番号のindex


def skin_read(vrm_pydata: vrm_types.VrmPydata) -> None:
    for skin in vrm_pydata.json.get("skins", []):
        vrm_pydata.skins_joints_list.append(skin["joints"])
        if "skeleton" in skin.keys():
            vrm_pydata.skins_root_node_list.append(skin["skeleton"])

    # node(ボーン)をパースする->親からの相対位置で記録されている


def node_read(vrm_pydata: vrm_types.VrmPydata) -> None:
    for i, node in enumerate(vrm_pydata.json["nodes"]):
        vrm_pydata.nodes_dict[i] = vrm2pydata_factory.bone(node)
        # TODO こっからorigin_bone
        if "mesh" in node.keys():
            vrm_pydata.origin_nodes_dict[i] = [vrm_pydata.nodes_dict[i], node["mesh"]]
            if "skin" in node.keys():
                vrm_pydata.origin_nodes_dict[i].append(node["skin"])
            else:
                print(node["name"] + "is not have skin")


def create_vrm_dict(data: bytes) -> Dict[str, Any]:
    vrm_json, binary_chunk = parse_glb(data)
    vrm_json["~accessors_decoded"] = decode_bin(vrm_json, binary_chunk)
    return vrm_json


def vrm_dict_diff(
    left: Any, right: Any, path: str, float_tolerance: float
) -> List[str]:
    if isinstance(left, list):
        if not isinstance(right, list):
            return [f"{path}: left is list but right is {type(right)}"]
        if len(left) != len(right):
            return [
                f"{path}: left length is {len(left)} but right length is {len(right)}"
            ]
        diffs = []
        for i, _ in enumerate(left):
            diffs.extend(
                vrm_dict_diff(left[i], right[i], f"{path}[{i}]", float_tolerance)
            )
        return diffs

    if isinstance(left, dict):
        if not isinstance(right, dict):
            return [f"{path}: left is dict but right is {type(right)}"]
        diffs = []
        for key in sorted(set(list(left.keys()) + list(right.keys()))):
            if key not in left:
                diffs.append(f"{path}: {key} not in left")
                continue
            if key not in right:
                diffs.append(f"{path}: {key} not in right")
                continue
            diffs.extend(
                vrm_dict_diff(
                    left[key], right[key], f'{path}["{key}"]', float_tolerance
                )
            )
        return diffs

    if isinstance(left, bool):
        if not isinstance(right, bool):
            return [f"{path}: left is bool but right is {type(right)}"]
        if left != right:
            return [f"{path}: left is {left} but right is {right}"]
        return []

    if isinstance(left, str):
        if not isinstance(right, str):
            return [f"{path}: left is str but right is {type(right)}"]
        if left != right:
            return [f'{path}: left is "{left}" but right is "{right}"']
        return []

    if left is None and right is not None:
        return [f"{path}: left is None but right is {type(right)}"]

    if isinstance(left, int) and isinstance(right, int):
        if left != right:
            return [f"{path}: left is {left} but right is {right}"]
        return []

    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        error = math.fabs(float(left) - float(right))
        if error > float_tolerance:
            return [
                f"{path}: left is {float(left):20.17f} but right is {float(right):20.17f}, error={error:19.17f}"
            ]
        return []

    raise Exception(f"{path}: unexpected type left={type(left)} right={type(right)}")


def vrm_diff(before: bytes, after: bytes, float_tolerance: float) -> List[str]:
    return vrm_dict_diff(
        create_vrm_dict(before), create_vrm_dict(after), "", float_tolerance
    )


if __name__ == "__main__":
    read_vrm(
        sys.argv[1],
        extract_textures_into_folder=True,
        make_new_texture_folder=True,
        license_check=True,
    )
