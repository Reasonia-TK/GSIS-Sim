"""MCP のツールの定義(引数の型と説明、注記)と、引数の検証。

引数は pydantic のモデルで検証し、既定値を埋めた辞書にしてエンジンに渡す(履歴にもこの形で残す)。
batch の各手順も、同じモデルで実行の前にすべて検証する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .engine import BATCH_EXCLUDED, OP_KINDS
from .errors import ToolFailure

Vec3 = Annotated[list[float], Field(min_length=3, max_length=3, description="[x, y, z]")]
Vec2 = Annotated[list[float], Field(min_length=2, max_length=2, description="断面の平面内の座標 [u, v]")]
Box6 = Annotated[list[float], Field(min_length=6, max_length=6, description="[xmin, ymin, zmin, xmax, ymax, zmax]")]
Ref = int | str
Refs = Annotated[list[Ref], Field(min_length=1, description="形状の名前、番号、または 'v:3'(体積)・'s:5'(面)の形の参照")]
Name = Annotated[str | None, Field(description="付ける名前(省略すると box1 などを自動で付ける)")]


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 入れ子の型
# ---------------------------------------------------------------------------


class PlaneSpec(Args):
    point: Vec3 = Field(description="平面上の点")
    normal: Vec3 = Field(description="平面の法線(向きはどちらでもよい)")


class FaceSelector(Args):
    """面の選び方(指定した条件をすべて満たす面。既定では計算領域の境界の面だけが対象)。"""

    faces: list[int] | None = Field(None, description="面の番号(list_faces の id)")
    volumes: list[Ref] | None = Field(None, description="この体積の面に限る")
    type: Literal["plane", "cylinder", "cone", "sphere", "torus", "bspline", "bezier", "revolution", "extrusion",
                  "other"] | None = Field(None, description="面の種類")
    on_plane: PlaneSpec | None = Field(None, description="この平面の上にある平らな面")
    normal: Vec3 | None = Field(None, description="外向き法線がこの向きの平らな面(angle_tol_deg 以内)")
    in_box: Box6 | None = Field(None, description="外接箱がこの箱に収まる面")
    contains_point: Vec3 | None = Field(None, description="この点を通る面(距離 tol 以内)")
    radius: float | None = Field(None, gt=0, description="円柱・球の面の半径")
    axis: Vec3 | None = Field(None, description="円柱の面の軸の向き")
    patch: str | None = Field(None, description="このパッチに入っている面")
    unassigned: bool | None = Field(None, description="true: どのパッチにも入っていない面")
    color: list[int] | None = Field(None, min_length=3, max_length=4, description="CAD で付いた面の色 [r, g, b(, a)](0〜255)")
    area_min: float | None = Field(None, ge=0, description="面積の下限")
    area_max: float | None = Field(None, gt=0, description="面積の上限")
    tol: float | None = Field(None, gt=0, description="長さの許容差(既定はモデルの大きさの 1e-6)")
    angle_tol_deg: float | None = Field(None, gt=0, le=90, description="向きの許容差 [deg](既定 1)")
    include_internal: bool | None = Field(None, description="true: 2 つの体積に挟まれた内部の面も対象にする")


class Segment(Args):
    """断面の輪の 1 区間(直前の点からつなぐ)。"""

    line_to: Vec2 | None = Field(None, description="この点まで直線")
    arc_to: Vec2 | None = Field(None, description="この点まで円弧(center か through のどちらかが要る)")
    center: Vec2 | None = Field(None, description="円弧の中心")
    through: Vec2 | None = Field(None, description="円弧が通る途中の点")
    ccw: bool = Field(True, description="center で指定した円弧の向き(true: u→v の向きに反時計回り)")

    @model_validator(mode="after")
    def _check(self):
        if (self.line_to is None) == (self.arc_to is None):
            raise ValueError("line_to か arc_to のどちらか 1 つを指定してください")
        if self.arc_to is not None and (self.center is None) == (self.through is None):
            raise ValueError("arc_to には center か through のどちらか 1 つが要ります")
        return self


class Circle(Args):
    center: Vec2
    radius: float = Field(gt=0)


class Loop(Args):
    """断面の閉じた輪。points(折れ線)、start + segments(直線と円弧)、circle(円)のどれか 1 つ。
    最後の点から始点へは自動で閉じる。"""

    points: list[Vec2] | None = Field(None, min_length=3, description="折れ線の頂点 [[u, v], ...]")
    start: Vec2 | None = Field(None, description="segments の始点")
    segments: list[Segment] | None = Field(None, min_length=1)
    circle: Circle | None = None

    @model_validator(mode="after")
    def _check(self):
        forms = [self.points is not None, self.segments is not None or self.start is not None, self.circle is not None]
        if sum(forms) != 1:
            raise ValueError("points、start + segments、circle のどれか 1 つを指定してください")
        if (self.segments is None) != (self.start is None):
            raise ValueError("start と segments は組で指定してください")
        return self


class Section(Args):
    point: Vec3 = Field(description="切る平面上の点")
    normal: Vec3 = Field(description="平面の法線。この向きの側を取り除いて断面を見せる")


class BatchStep(Args):
    tool: str = Field(description="ツールの名前(add_box など)")
    args: dict[str, Any] = Field(default_factory=dict, description="そのツールの引数")


# ---------------------------------------------------------------------------
# ツールの引数
# ---------------------------------------------------------------------------


class ModelNewArgs(Args):
    name: str = Field("model", pattern=r"^[A-Za-z0-9_.\-]{1,64}$", description="モデルの名前(画像のファイル名などに使う)")
    unit: Literal["m", "cm", "mm", "um"] = Field("mm", description="長さの単位(.pmesh を書くときに m に換算する)")


class ModelInfoArgs(Args):
    history: bool = Field(False, description="true: 手順の一覧も返す")


class PathArgs(Args):
    path: str = Field(description="作業フォルダからの相対パス")


class UndoArgs(Args):
    steps: int = Field(1, ge=1, le=1000, description="取り消す手順の数(batch は 1 手順)")


class BatchArgs(Args):
    steps: list[BatchStep] = Field(min_length=1, max_length=500, description="順に実行する手順")
    verbose: bool = Field(False, description="true: 各手順の結果をすべて返す(既定は短い要約)")


class AddBoxArgs(Args):
    corner: Vec3 = Field(description="最小の角 [x, y, z]")
    size: Vec3 = Field(description="大きさ [dx, dy, dz](正)")
    name: Name = None


class AddCylinderArgs(Args):
    base: Vec3 = Field(description="底面の中心")
    axis: Vec3 = Field(description="底面の中心から上面の中心へのベクトル(長さが高さ)")
    radius: float = Field(gt=0)
    angle_deg: float = Field(360.0, gt=0, le=360, description="中心角 [deg]")
    name: Name = None


class AddSphereArgs(Args):
    center: Vec3
    radius: float = Field(gt=0)
    name: Name = None


class AddConeArgs(Args):
    base: Vec3 = Field(description="底面の中心")
    axis: Vec3 = Field(description="底面の中心から上面の中心へのベクトル")
    radius1: float = Field(ge=0, description="底面の半径")
    radius2: float = Field(ge=0, description="上面の半径(0 で尖る)")
    name: Name = None


class AddTorusArgs(Args):
    center: Vec3
    radius_major: float = Field(gt=0, description="中心から管の中心までの半径")
    radius_minor: float = Field(gt=0, description="管の半径")
    axis: Vec3 = Field([0.0, 0.0, 1.0], description="回転軸の向き")
    name: Name = None


class AddProfileArgs(Args):
    plane: Literal["xy", "xz", "yz"] = Field("xy", description="xy: u=x, v=y / xz: u=x, v=z / yz: u=y, v=z")
    origin: Vec3 = Field([0.0, 0.0, 0.0], description="平面の原点(u = v = 0 の点)")
    u_axis: Vec3 | None = Field(None, description="u の向き(plane の代わりに任意の平面を使うとき。v_axis と組で)")
    v_axis: Vec3 | None = Field(None, description="v の向き")
    outer: Loop = Field(description="外側の輪")
    holes: list[Loop] = Field(default_factory=list, description="穴の輪")
    name: Name = None


class ExtrudeArgs(Args):
    entities: Refs = Field(description="押し出す面(断面の名前など)")
    vector: Vec3 = Field(description="押し出す向きと長さ")
    name: Name = None


class RevolveArgs(Args):
    entities: Refs = Field(description="回す面(断面の名前など)")
    axis_point: Vec3 = Field([0.0, 0.0, 0.0], description="回転軸上の点")
    axis_dir: Vec3 = Field([0.0, 0.0, 1.0], description="回転軸の向き")
    angle_deg: float = Field(360.0, description="回す角度 [deg](0 < |角度| ≤ 360)")
    name: Name = None


class BooleanArgs(Args):
    op: Literal["fuse", "cut", "intersect", "fragment"] = Field(description="fuse: 和、cut: 差、intersect: 積、fragment: 分割(接する面を共有させる)")
    objects: Refs = Field(description="対象(cut では引かれる側)")
    tools: list[Ref] = Field(default_factory=list, description="道具(cut では引く側)")
    keep_objects: bool = False
    keep_tools: bool = False
    name: Name = None


class TransformArgs(Args):
    entities: Refs
    op: Literal["translate", "rotate", "scale", "mirror"]
    vector: Vec3 | None = Field(None, description="translate: 移動のベクトル")
    point: Vec3 = Field([0.0, 0.0, 0.0], description="rotate: 軸上の点、scale: 中心、mirror: 鏡の面上の点")
    axis: Vec3 | None = Field(None, description="rotate: 回転軸の向き")
    angle_deg: float | None = Field(None, description="rotate: 角度 [deg]")
    factor: float | Vec3 | None = Field(None, description="scale: 倍率(数か [fx, fy, fz])")
    normal: Vec3 | None = Field(None, description="mirror: 鏡の面の法線")
    duplicate: bool = Field(False, description="true: 写しを作って変形する(元は残る)")
    name: str | None = Field(None, description="duplicate=true のときの写しの名前")


class RemoveArgs(Args):
    entities: Refs


class ImportCadArgs(Args):
    path: str = Field(description="STEP / IGES / BREP のファイル(作業フォルダか --read-dir の中)")
    heal: bool = Field(False, description="true: OpenCASCADE の修復(退化面の除去、縫い合わせなど)を有効にして読む")
    name: Name = None


class RunPythonArgs(Args):
    code: str = Field(min_length=1, max_length=200_000, description="実行する Python のコード")


class MeasureArgs(Args):
    entities: list[Ref] | None = Field(None, description="測る体積(省略するとすべて)")
    expected_volume: float | None = Field(None, gt=0, description="期待する体積の合計(相対誤差を返す)")
    rtol: float | None = Field(None, gt=0, description="相対誤差がこれを超えたらエラーにする(batch の検算用)")


class ListFacesArgs(Args):
    select: FaceSelector | None = Field(None, description="条件(省略するとすべての面)")
    limit: int = Field(60, ge=1, le=500)
    offset: int = Field(0, ge=0)


class CheckGeometryArgs(Args):
    mesh_test: bool = Field(False, description="true: 粗い面のメッシュを作り、各体積の面が閉じているかも確かめる")


class AssignPatchArgs(Args):
    name: str = Field(description="パッチの名前(.pmesh のパッチ名。inlet・outlet・wall など)")
    select: FaceSelector = Field(description="面の選び方")
    kind: Literal["wall", "inlet", "outlet", "symmetry", "patch"] | None = Field(
        None, description="パッチの種類(省略すると名前から推定: wall*・inlet*・outlet*・sym*、それ以外は patch)")
    mode: Literal["set", "add", "remove"] = Field("set", description="set: この面だけにする、add: 足す、remove: 外す")
    steal: bool = Field(False, description="true: 別のパッチに入っている面を移す")


class RemovePatchArgs(Args):
    name: str = Field(description="消すパッチの名前('*' ですべて)")


class NoArgs(Args):
    pass


class RenderArgs(Args):
    target: Literal["geometry", "mesh"] = Field("geometry", description="geometry: 形状、mesh: 生成したメッシュ")
    view: Literal["auto", "iso", "iso_back", "front", "back", "right", "left", "top", "bottom", "custom"] = Field(
        "auto", description="視点(auto: 断面なしは iso、断面ありは取り除いた側から斜めに)")
    azimuth: float | None = Field(None, description="方位角 [deg](+x から +y へ)。指定すると view より優先")
    elevation: float | None = Field(None, description="仰角 [deg]")
    direction: Vec3 | None = Field(None, description="カメラのある向き(対象からカメラへ)。最優先")
    section: Section | None = Field(None, description="断面(平面の法線の側を取り除く)")
    color_by: Literal["patch", "face", "volume"] = "patch"
    labels: Literal["auto", "faces", "patches", "none"] = Field("auto", description="文字(geometry の auto は面の番号)")
    focus: Box6 | None = Field(None, description="この箱が画面に収まるように拡大する")
    zoom: float = Field(1.0, gt=0, le=100)
    width: int = Field(900, ge=200, le=2400)
    height: int = Field(700, ge=200, le=2400)
    path: str | None = Field(None, description="書き出す PNG(省略すると renders/ に連番で)")
    inline: bool = Field(False, description="true: 画像も応答に入れる(既定はパスだけ)")


class MeshSettingsArgs(Args):
    size_max: float | None = Field(None, gt=0, description="最大の要素寸法(既定はモデルの外接箱の対角の 1/20)")
    size_min: float | None = Field(None, ge=0, description="最小の要素寸法")
    curvature_points: int | None = Field(None, ge=0, le=360, description="曲面で円周あたりの要素数(0 で使わない)")
    algorithm_3d: Literal["delaunay", "hxt", "frontal"] | None = None
    algorithm_2d: Literal["frontal-delaunay", "delaunay", "meshadapt"] | None = None
    optimize: Literal["netgen", "gmsh", "none"] | None = Field(None, description="3 次元メッシュの最適化(既定 netgen)")
    extend_from_boundary: bool | None = Field(None, description="境界の寸法を内部に広げる(gmsh の既定 true)")
    threads: int | None = Field(None, ge=1, le=64)
    reset: bool = Field(False, description="true: 既定値に戻してから設定する")


class MeshRefineArgs(Args):
    name: str = Field(description="細分化の名前(同じ名前で呼ぶと置き換え。remove で '*' はすべて)")
    target: Literal["faces", "box", "ball", "cylinder"] | None = Field(
        None, description="faces: 面からの距離、box: 箱の中、ball: 球の中、cylinder: 円柱の中")
    select: FaceSelector | None = Field(None, description="faces: 対象の面")
    size: float | None = Field(None, gt=0, description="細かい部分の要素寸法")
    size_far: float | None = Field(None, gt=0, description="外側の要素寸法(既定は size_max)")
    dist_min: float = Field(0.0, ge=0, description="faces: この距離までは size")
    dist_max: float | None = Field(None, gt=0, description="faces: この距離で size_far(間は線形)")
    box: Box6 | None = None
    center: Vec3 | None = Field(None, description="ball: 中心")
    radius: float | None = Field(None, gt=0, description="ball・cylinder: 半径")
    base: Vec3 | None = Field(None, description="cylinder: 底面の中心")
    axis: Vec3 | None = Field(None, description="cylinder: 底面の中心から上面の中心へのベクトル")
    thickness: float = Field(0.0, ge=0, description="box・ball: 外側へ size_far になるまでの幅")
    sampling: int | None = Field(None, ge=2, le=2000, description="faces: 距離を測る点の数(1 辺あたり。既定は自動)")
    remove: bool = Field(False, description="true: この名前の細分化を消す")


class MeshGenerateArgs(Args):
    dim: Literal[2, 3] = Field(3, description="3: 四面体、2: 面だけ")
    max_cells: int = Field(2_000_000, ge=1, description="四面体の数の見積りがこれを超えたら作らない")


class ExportPmeshArgs(Args):
    path: str = Field(description="書き出す .pmesh(作業フォルダからの相対パス)")
    patch_order: list[str] | None = Field(None, description="パッチの並び(省略すると付けた順)")
    renumber: bool = Field(True, description="セル番号を逆 Cuthill–McKee 順に付け直す")
    report: str | None = Field(None, description="品質レポート(.md)も書く")
    vtu: str | None = Field(None, description="セルの VTU も書く")
    boundary_vtu: str | None = Field(None, description="境界面の VTU も書く")


class ExportArgs(Args):
    path: str = Field(description="拡張子で形式を決める: .step .stp .iges .igs .brep(形状)、.stl .msh .vtu(メッシュ)")
    binary: bool = Field(False, description=".stl・.msh をバイナリで書く")


class GuiSurfaceArgs(Args):
    known: str | None = Field(None, description="前回の key(形状とメッシュが変わっていなければ三角形を省く)")


# ---------------------------------------------------------------------------
# ツールの一覧
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    model: type[Args]
    description: str

    @property
    def kind(self) -> str:
        return OP_KINDS[self.name]


TOOLS: list[ToolSpec] = [
    # モデル
    ToolSpec("model_new", "新しいモデル", ModelNewArgs,
             "新しい空のモデルを作る(今のモデルと履歴は消える)。最初に呼んで長さの単位を決める。"),
    ToolSpec("model_info", "モデルの状態", ModelInfoArgs,
             "モデルの要約: 体積の一覧と体積、名前、境界パッチ、付いていない面、メッシュの有無、細分化。"),
    ToolSpec("model_save", "状態の保存", PathArgs,
             "モデルの状態(手順の履歴)を JSON に保存する。model_load で読むと同じ番号の形状を作り直す。"),
    ToolSpec("model_load", "状態の読み込み", PathArgs,
             "model_save で保存した JSON を読み、手順をやり直して状態を戻す(今のモデルは置き換わる)。"),
    ToolSpec("undo", "元に戻す", UndoArgs,
             "最後の手順を取り消す(batch はまとめて 1 手順)。形状の手順を取り消すとメッシュは消える。"),
    ToolSpec("batch", "まとめて実行", BatchArgs,
             "複数の手順を順に実行する。1 つでも失敗したら batch 全体を元に戻し、失敗した手順の番号(index、0 始まり)と"
             "理由を返す。steps の各要素は {tool, args}(tool はこのサーバーのツール名、args はその引数)。"
             "measure に expected_volume と rtol を入れると検算の手順になる。model_new・model_load・model_save・undo・"
             "batch は入れられない。"),
    # 形状
    ToolSpec("add_box", "箱", AddBoxArgs, "直方体を作る。"),
    ToolSpec("add_cylinder", "円柱", AddCylinderArgs,
             "円柱を作る。例: x 軸方向で長さ 10、半径 1 → base=[0,0,0], axis=[10,0,0], radius=1。"),
    ToolSpec("add_sphere", "球", AddSphereArgs, "球を作る。"),
    ToolSpec("add_cone", "円錐", AddConeArgs, "円錐・円錐台を作る。"),
    ToolSpec("add_torus", "トーラス", AddTorusArgs, "トーラスを作る。"),
    ToolSpec("add_profile", "断面(2 次元)", AddProfileArgs,
             "平面上の閉じた断面(面)を作る。extrude・revolve で立体にする。折れ線は outer={points: [[u,v],...]}、"
             "直線と円弧は outer={start:[u,v], segments:[{line_to:[u,v]}, {arc_to:[u,v], center:[u,v]}, ...]}、"
             "円は outer={circle:{center, radius}}。回転体の子午面は plane='xz'(u=x が半径、v=z が軸)で作り、"
             "revolve(軸は z)する。面積・重心と、v 軸まわりに回したときの体積(パップスの定理)を返す。"),
    ToolSpec("extrude", "押し出し", ExtrudeArgs, "面(断面)をベクトルの向きに押し出して立体にする。"),
    ToolSpec("revolve", "回転", RevolveArgs,
             "面(断面)を軸まわりに回して立体にする。360° のときは元の断面を消す。断面が平らで軸がその平面内にあれば、"
             "パップスの定理の体積と相対誤差も返す。"),
    ToolSpec("boolean", "ブーリアン", BooleanArgs,
             "立体(または面)の和・差・積・分割。既定では入力は消え、結果は name(省略すると objects の最初の名前)になる。"),
    ToolSpec("transform", "移動・回転・拡大・鏡映", TransformArgs,
             "形状を移動・回転・拡大縮小・鏡映する(番号は変わらない)。duplicate=true で写しを作って変形する。"),
    ToolSpec("remove", "削除", RemoveArgs, "形状を消す。"),
    ToolSpec("import_cad", "CAD の読み込み", ImportCadArgs,
             "STEP・IGES・BREP を読む(STEP・IGES はモデルの単位に換算)。ファイルは作業フォルダの .gsis_mcp/imports に"
             "写して履歴から読み直せるようにする。面積 0 の面があれば知らせる。"),
    ToolSpec("run_python", "gmsh スクリプトの実行(危険)", RunPythonArgs,
             "gmsh の Python API を使うコードを実行する(変数 gmsh・occ・np・math・names。names.set('名前', [(3, tag)]) "
             "で名前を付けられる)。任意のコードが動くので、サーバーを --allow-python で起動したときだけ使える。"),
    # 計測・検査
    ToolSpec("measure", "計測", MeasureArgs,
             "体積ごとの体積・表面積・重心・外接箱・面の数・殻の数(空洞があると 2 以上)と合計。expected_volume を"
             "与えると相対誤差を返す。"),
    ToolSpec("list_faces", "面の一覧", ListFacesArgs,
             "面の一覧: id(番号)、type、area、c(重心)、n(平らな面の外向き法線)、r・axis(円柱)、r・center(球)、bbox、"
             "patch。select を与えると、その条件に合う面だけ(assign_patch の試し)。"),
    ToolSpec("check_geometry", "妥当性の検査", CheckGeometryArgs,
             "立体の検査: 体積が正か、面積 0 の面、長さ 0 の曲線、体積に属さない面、重なっている体積。"
             "mesh_test=true で面のメッシュが閉じるかも確かめる。"),
    # 境界パッチ
    ToolSpec("assign_patch", "境界パッチの付与", AssignPatchArgs,
             "条件で選んだ境界の面にパッチ名を付ける。最後に select={unassigned: true} で残りを壁にまとめるとよい。"
             "1 つの面は 1 つのパッチにだけ入る。付いていない面の数と番号を返す。"),
    ToolSpec("remove_patch", "パッチの削除", RemovePatchArgs, "パッチを消す('*' ですべて)。"),
    ToolSpec("list_patches", "パッチの一覧", NoArgs,
             "パッチごとの面と面積、付いていない面の一覧(complete=true ならすべての境界の面にパッチがある)。"),
    # 画像
    ToolSpec("render", "画像", RenderArgs,
             "形状(面の色はパッチ、文字は面の番号)やメッシュの画像を PNG で作業フォルダに書き、パスを返す。"
             "section で断面(切り口は灰色で塗る)、focus で部分の拡大。画面は出さない。"),
    # メッシュ
    ToolSpec("mesh_settings", "メッシュの設定", MeshSettingsArgs,
             "四面体メッシュの全体の設定(最大・最小の寸法、曲率による細分化、アルゴリズム、最適化)。"
             "指定した項目だけ変える。"),
    ToolSpec("mesh_refine", "場所ごとの細かさ", MeshRefineArgs,
             "場所ごとの要素寸法: faces(面から dist_min まで size、dist_max で size_far)、box・ball・cylinder(中は size、"
             "外は size_far)。複数あれば最も細かいものが効く。"),
    ToolSpec("mesh_generate", "メッシュの生成", MeshGenerateArgs,
             "四面体メッシュを作る。四面体の数を先に見積り、max_cells を超えるなら作らない。要素数、品質(minSICN)、"
             "メッシュの体積と形状の体積の差、パッチごとの三角形の数を返す。"),
    ToolSpec("mesh_quality", "メッシュの品質", NoArgs,
             "メッシュを .pmesh と同じ形に変換して、セル数・セル寸法・非直交角・歪み・体積比・形式の検査を返す。"
             "すべての境界の面にパッチが要る。"),
    ToolSpec("export_pmesh", ".pmesh の書き出し", ExportPmeshArgs,
             "ソルバー用の .pmesh を書く(座標は m に換算)。書く前に形式の検査を通す。品質レポート・VTU も書ける。"),
    ToolSpec("export", "書き出し", ExportArgs,
             "形状(STEP・IGES・BREP)やメッシュ(STL・MSH 4.1・VTU)を書く。STEP・IGES は単位付き(読むときに換算される)、"
             "BREP・STL・MSH・VTU の座標はモデルの単位の数値のまま。MSH と STL にはパッチ名(Physical Group)が入る。"),
    # GUI 専用(サーバーを --gui-tools で起動したときだけ一覧に出す。GUI は AI に渡すツールの一覧から外す)
    ToolSpec("gui_surface", "GUI の表示用の表面", GuiSurfaceArgs,
             "GUI 専用。3D 表示用に、表面の三角形(面の番号付き)・面の情報・パッチを返す。"),
]
TOOL_BY_NAME = {t.name: t for t in TOOLS}
GUI_ONLY_TOOLS = frozenset({"gui_surface"})
GUI_ONLY_META = {"gsis-pbc/gui-only": True}  # tools/list の _meta に入れる印(クライアントが AI に渡さないため)


def annotations_for(spec: ToolSpec) -> dict[str, Any]:
    kind = spec.kind
    ann: dict[str, Any] = {"title": spec.title, "open_world_hint": False}
    if kind == "query":
        ann["read_only_hint"] = True
    else:
        ann["read_only_hint"] = False
        ann["destructive_hint"] = spec.name in ("model_new", "model_load", "undo", "remove", "export",
                                                "export_pmesh", "model_save", "run_python")
        ann["idempotent_hint"] = spec.name in ("mesh_settings", "assign_patch", "remove_patch", "model_save",
                                               "export", "export_pmesh", "mesh_refine")
    return ann


def _compact(node: Any, *, in_props: bool = False) -> Any:
    """スキーマを小さくする(tools/list の応答がエージェントの文脈を圧迫しないように)。

    - pydantic が付ける "title" を除く
    - 省略できる引数の ``anyOf: [X, {"type": "null"}]`` と ``"default": null`` を X だけにする
      (null を送っても pydantic の検証では受け付ける)
    - 外側に説明があるときは、内側の型の説明(``[x, y, z]`` など)を除く
    """
    if isinstance(node, list):
        return [_compact(v) for v in node]
    if not isinstance(node, dict):
        return node
    if in_props:
        return {k: _compact(v) for k, v in node.items()}
    any_of = node.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
        other = next(x for x in any_of if x != {"type": "null"})
        merged = dict(other)
        for k, v in node.items():
            if k in ("anyOf",) or (k == "default" and v is None):
                continue
            merged[k] = v
        node = merged
    out = {}
    for k, v in node.items():
        if k == "title" and isinstance(v, str):
            continue
        if k == "default" and v is None:
            continue
        if k == "properties":
            out[k] = {pk: _compact(pv) for pk, pv in v.items()}
        elif k == "$defs":
            out[k] = {dk: _compact(dv) for dk, dv in v.items()}
        else:
            out[k] = _compact(v)
    if "description" in out and out.get("type") == "array" and isinstance(out.get("items"), dict):
        pass
    for key in ("items",):
        inner = out.get(key)
        if isinstance(inner, dict) and "description" in out and "description" in inner and "$ref" not in inner:
            inner.pop("description", None)
    return out


def input_schema(spec: ToolSpec) -> dict[str, Any]:
    return _compact(spec.model.model_json_schema())


def _format_validation(exc: ValidationError, prefix: str = "") -> str:
    parts = []
    for e in exc.errors()[:8]:
        loc = ".".join(str(x) for x in e.get("loc", ()))
        msg = e.get("msg", "")
        parts.append(f"{prefix}{loc}: {msg}" if loc else f"{prefix}{msg}")
    more = len(exc.errors()) - 8
    if more > 0:
        parts.append(f"…ほか {more} 件")
    return "; ".join(parts)


def normalize(tool: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """引数を検証し、既定値を埋めた辞書にする(batch の steps は (ツール名, 引数) の組の並びにする)。"""
    spec = TOOL_BY_NAME.get(tool)
    if spec is None:
        raise ToolFailure(f"未知のツールです: {tool}", hint=f"使えるツール: {', '.join(TOOL_BY_NAME)}")
    try:
        m = spec.model.model_validate(args or {})
    except ValidationError as exc:
        raise ToolFailure(f"{tool} の引数が不正です: {_format_validation(exc)}") from exc
    data = m.model_dump(mode="python")
    if tool == "batch":
        steps = []
        for i, st in enumerate(m.steps):
            if st.tool in BATCH_EXCLUDED:
                raise ToolFailure(f"batch の {i} 番目の手順: {st.tool} は batch の中では使えません",
                                  data={"failed": {"index": i, "tool": st.tool}})
            sub = TOOL_BY_NAME.get(st.tool)
            if sub is None:
                raise ToolFailure(f"batch の {i} 番目の手順: 未知のツール {st.tool!r}",
                                  data={"failed": {"index": i, "tool": st.tool}})
            try:
                sm = sub.model.model_validate(st.args)
            except ValidationError as exc:
                raise ToolFailure(f"batch の {i} 番目の手順({st.tool})の引数が不正です: {_format_validation(exc)}",
                                  data={"failed": {"index": i, "tool": st.tool}}) from exc
            steps.append((st.tool, sm.model_dump(mode="python")))
        data["steps"] = steps
    return data


def available_tools(allow_python: bool, gui_tools: bool = False) -> list[ToolSpec]:
    return [t for t in TOOLS
            if (allow_python or t.name != "run_python") and (gui_tools or t.name not in GUI_ONLY_TOOLS)]


INSTRUCTIONS = """\
gmsh(OpenCASCADE)で計算領域の形状を作り、境界パッチを付けて、ソルバー GSIS-PBC 用のメッシュ(.pmesh)を書き出すツールです。

基本の流れ:
1. model_new(unit で長さの単位を決める)
2. 形状: add_box / add_cylinder / add_sphere / add_cone / add_torus、add_profile(2 次元の断面)→ extrude / revolve、boolean(和・差・積)、transform、import_cad
3. 検算: measure(expected_volume に解析値)、check_geometry、render(section で断面)
4. 境界パッチ: list_faces で面を見て assign_patch(on_plane・normal・radius・in_box・contains_point などで選ぶ)。最後に select={"unassigned": true} で残りを壁にまとめ、list_patches で未割り当てが 0 か確かめる
5. メッシュ: mesh_settings・mesh_refine → mesh_generate → mesh_quality → export_pmesh(m に換算して書く)

約束:
- 長さはモデルの単位、角度は度、ベクトルは [x, y, z]。.pmesh だけ m に換算する
- 形状は名前で参照できる(各ツールの name。省略すると box1 などを自動で付ける)。数の番号は、boolean・transform・measure では体積、extrude・revolve では面。'v:3'・'s:5' と書くと次元を明示できる
- 断面の plane: xy(u=x, v=y)、xz(u=x, v=z)、yz(u=y, v=z)。回転体は xz 面の子午面の断面(u が半径)を z 軸まわりに revolve する
- 複数の手順は batch にまとめると、途中で失敗したとき全体を元に戻し、失敗した手順の番号と理由を返す。undo は最後の手順(batch は 1 手順)を取り消す
- 形状を変えると、変わった面はパッチから外れ、メッシュは消える。パッチは形状を確定させてから付ける
- ファイルは作業フォルダの中(相対パス)にだけ書ける。読み込みは作業フォルダと --read-dir の中だけ
"""
