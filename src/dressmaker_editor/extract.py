"""Extract the :class:`~.catalog.Catalog` from an installed copy of Dressmaker.

All items, characters and quests are ``ScriptableEnum`` assets in
``resources.assets``. The build strips Unity's type trees, so field layouts
are rebuilt from the game's own DLLs with TypeTreeGeneratorAPI. The campaign
(main story order and side quests) lives in one of the ``sharedassets``
files, and story flags are plain C# enums read from ``Assembly-CSharp.dll``.
"""

from __future__ import annotations

import contextlib
import logging
import struct
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .catalog import GARMENT_TYPES, SCHEMA, Catalog
from .paths import GameInstall

log = logging.getLogger(__name__)

Progress = Callable[[str], None]

_HEADER_FIELDS = ("m_GameObject", "m_Enabled", "m_Script", "m_Name")


def extract_catalog(install: GameInstall, progress: Progress | None = None) -> Catalog:
    report = progress or (lambda _msg: None)
    started = time.perf_counter()

    report("Reading the game's scripts…")
    enums, bases = read_assembly_info(install.managed_dir / "Assembly-CSharp.dll")

    import UnityPy
    from UnityPy.enums import ClassIDType

    data_dir = install.data_dir
    report("Loading game data…")
    scene_files = sorted(
        p
        for p in data_dir.iterdir()
        if p.is_file()
        and p.name.startswith(("level", "sharedassets"))
        and p.suffix not in (".resS", ".resource")
    )
    env = UnityPy.load(
        str(data_dir / "resources.assets"),
        str(data_dir / "globalgamemanagers.assets"),
        *map(str, scene_files),
    )
    files = {Path(name).name: f for name, f in env.files.items()}
    resources_file = files["resources.assets"]
    unity_version = str(resources_file.unity_version)

    report("Sorting game objects…")
    found = []  # (file name, object reader, class name, namespace, assembly)
    for file_name, serialized in files.items():
        for obj in serialized.objects.values():
            if obj.type != ClassIDType.MonoBehaviour:
                continue
            try:
                script = obj.read(check_read=False).m_Script.read()
            except Exception:  # scripts in files we didn't load, editor-only scripts
                continue
            found.append(
                (file_name, obj, script.m_ClassName, script.m_Namespace, script.m_AssemblyName)
            )

    generator = _make_generator(unity_version, install.managed_dir)
    env.typetree_generator = generator

    assets: dict[str, dict[str, Any]] = {}
    raw_by_path_id: dict[int, tuple[str, dict[str, Any]]] = {}
    campaigns: list[tuple[str, dict[str, Any]]] = []
    failures = 0

    report("Reading items, characters and quests…")
    with _serialize_reference_support(generator):
        for file_name, obj, cls, ns, asm in found:
            fullname = f"{ns}.{cls}" if ns else cls
            if file_name == "resources.assets":
                node = _nodes(generator, asm, fullname)
                if node is None or not _has_field(node, "_guid"):
                    continue
                try:
                    tree = obj.read_typetree(node, check_read=False)
                except Exception as exc:
                    failures += 1
                    log.debug("falling back to raw read for %s %s: %s", cls, obj.path_id, exc)
                    tree = _raw_name_and_guid(obj)
                    if tree is None:
                        continue
                guid = tree.get("_guid")
                if not guid:
                    continue
                raw_by_path_id[obj.path_id] = (cls, tree)
            elif cls == "CampaignDefinition":
                try:
                    campaigns.append((file_name, obj.read_typetree(check_read=False)))
                except Exception as exc:
                    log.warning("could not read campaign in %s: %s", file_name, exc)

        guid_by_path_id = {pid: tree["_guid"] for pid, (_cls, tree) in raw_by_path_id.items()}

        def resolve(pptr: Any) -> str | None:
            if not isinstance(pptr, dict) or "m_PathID" not in pptr:
                return None
            if pptr.get("m_FileID", 0) != 0:
                return None
            return guid_by_path_id.get(pptr["m_PathID"])

        for cls, tree in raw_by_path_id.values():
            entry = _asset_entry(cls, tree, resolve)
            assets[entry["guid"]] = entry

        campaign = _pick_campaign(campaigns, files, guid_by_path_id)

    elapsed = time.perf_counter() - started
    log.info("extracted %d assets in %.1fs (%d raw fallbacks)", len(assets), elapsed, failures)
    return Catalog(
        {
            "schema": SCHEMA,
            "source": {
                "build_id": install.build_id,
                "unity_version": unity_version,
                "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "fallback_reads": failures,
            },
            "assets": assets,
            "bases": bases,
            "enums": enums,
            "campaign": campaign,
        }
    )


# ── type trees ────────────────────────────────────────────────────────────
def _make_generator(unity_version: str, managed_dir: Path):
    from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

    generator = TypeTreeGenerator(unity_version)
    for dll in sorted(managed_dir.glob("*.dll")):
        # Yarn Spinner ships private copies of System.* assemblies under the same
        # assembly names; loading both makes the generator refuse the second.
        if dll.name.startswith(("Yarn.System", "Yarn.Microsoft")):
            continue
        try:
            generator.load_dll(dll.read_bytes())
        except Exception as exc:
            log.debug("skipped %s: %s", dll.name, exc)
    return generator


_node_cache: dict[tuple[int, str, str], Any] = {}


def _nodes(generator, assembly: str, fullname: str):
    key = (id(generator), assembly, fullname)
    if key not in _node_cache:
        try:
            _node_cache[key] = generator.get_nodes_up(assembly, fullname)
        except Exception:
            _node_cache[key] = None
    return _node_cache[key]


def _has_field(node, name: str) -> bool:
    return any(child.m_Name == name for child in node.m_Children)


@contextlib.contextmanager
def _serialize_reference_support(generator) -> Iterator[None]:
    """Teach UnityPy to read ``[SerializeReference]`` fields without type trees.

    UnityPy looks referenced types up in the file's own type table, which a
    stripped build doesn't have. Generate them from the DLLs instead. The
    generator always prepends a MonoBehaviour header, which plain serializable
    classes don't have, so drop it. The native fast path can't call back into
    Python, so it is switched off while this is active.
    """
    from UnityPy.helpers import TypeTreeHelper
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode

    cache: dict[tuple[str, str, str], Any] = {}

    def get_ref_type_node(ref_object, _assetfile):
        typ = ref_object["type"]
        if isinstance(typ, dict):
            cls, ns, asm = typ["class"], typ["ns"], typ["asm"]
        else:
            cls, ns, asm = getattr(typ, "class"), typ.ns, typ.asm
        if not cls:
            return None
        key = (asm, ns, cls)
        if key not in cache:
            assembly = asm if asm.endswith(".dll") else f"{asm}.dll"
            raw = generator.get_nodes(assembly, f"{ns}.{cls}" if ns else cls)
            kept = []
            skipping = False
            for node in raw:
                if node.m_Level == 1:
                    skipping = node.m_Name in _HEADER_FIELDS
                if skipping and node.m_Level >= 1:
                    continue
                kept.append(
                    TypeTreeNode(
                        node.m_Level, node.m_Type, node.m_Name, 0, 0, m_MetaFlag=node.m_MetaFlag
                    )
                )
            cache[key] = TypeTreeNode.from_list(kept)
        return cache[key]

    saved = (TypeTreeHelper.get_ref_type_node, TypeTreeHelper.read_typetree_boost)
    TypeTreeHelper.get_ref_type_node = get_ref_type_node
    TypeTreeHelper.read_typetree_boost = None
    try:
        yield
    finally:
        TypeTreeHelper.get_ref_type_node, TypeTreeHelper.read_typetree_boost = saved


def _raw_name_and_guid(obj) -> dict[str, Any] | None:
    """Read just ``m_Name`` and ``_guid`` from a ScriptableEnum's raw bytes.

    The layout is the MonoBehaviour header (two PPtrs, a bool, the name)
    followed by ScriptableEnum's first field, ``_guid``.
    """
    try:
        data = obj.get_raw_data()
        pos = 12  # m_GameObject: int32 file id + int64 path id
        pos += 4  # m_Enabled, aligned
        pos += 12  # m_Script
        name, pos = _read_aligned_string(data, pos)
        guid, _ = _read_aligned_string(data, pos)
    except struct.error, UnicodeDecodeError, IndexError:
        return None
    if len(guid) != 32:
        return None
    return {"m_Name": name, "_guid": guid}


def _read_aligned_string(data: bytes, pos: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("<i", data, pos)
    pos += 4
    text = data[pos : pos + length].decode("utf-8")
    pos += length
    pos += (-pos) % 4
    return text, pos


# ── asset entries ─────────────────────────────────────────────────────────
def _pptr_set(value: Any) -> bool:
    return isinstance(value, dict) and value.get("m_PathID", 0) != 0


def _asset_entry(cls: str, tree: dict[str, Any], resolve) -> dict[str, Any]:
    entry: dict[str, Any] = {"guid": tree["_guid"], "name": tree["m_Name"], "type": cls}
    pretty = tree.get("prettyName")
    if isinstance(pretty, str) and pretty.strip():
        entry["pretty"] = pretty.strip()
    if cls == "Fabric":
        entry["cost"] = tree.get("cost")
        entry["unlockLevel"] = tree.get("prestigeUnlockLevel")
        entry["usable"] = _pptr_set(tree.get("material"))
    elif cls == "AccessoryDefinition":
        entry["cost"] = tree.get("cost")
        entry["unlockLevel"] = tree.get("prestigeUnlockLevel")
        entry["usable"] = _pptr_set(tree.get("prefab"))
    elif cls == "QuestDefinition":
        entry["giver"] = resolve(tree.get("questGiverCharacter"))
        entry["budget"] = tree.get("budget", 0)
        entry["requirements"] = _requirements(tree, resolve)
    elif cls == "CharacterDefinition":
        entry["giftQuest"] = resolve(tree.get("giftQuest"))
    elif cls in GARMENT_TYPES:
        entry["variants"] = [len(v.get("panels", [])) for v in tree.get("variations", [])]
    elif cls == "GossipReportDefinition":
        entry["prestigeReward"] = tree.get("prestigeReward")
    return entry


def _requirements(tree: dict[str, Any], resolve) -> list[dict[str, Any]]:
    registry = {
        ref["rid"]: ref for ref in (tree.get("references") or {}).get("RefIds", []) if "rid" in ref
    }
    out = []
    for item in tree.get("requirements", []):
        rid = item.get("rid") if isinstance(item, dict) else item
        ref = registry.get(rid)
        if not ref:
            continue
        typ = ref["type"]
        req: dict[str, Any] = {
            "type": typ["class"] if isinstance(typ, dict) else getattr(typ, "class")
        }
        for key, value in (ref.get("data") or {}).items():
            req[key] = resolve(value) if _is_pptr(value) else value
        out.append(req)
    return out


def _is_pptr(value: Any) -> bool:
    return isinstance(value, dict) and "m_PathID" in value and "m_FileID" in value


def _pick_campaign(campaigns, files, guid_by_path_id) -> dict[str, Any]:
    """Turn the full game's CampaignDefinition into guid lists."""
    if not campaigns:
        return {}

    def chosen():
        for file_name, tree in campaigns:
            if tree.get("m_Name") == "FullCampaign":
                return file_name, tree
        return max(campaigns, key=lambda c: len(c[1].get("quests", [])))

    file_name, tree = chosen()
    externals = files[file_name].externals

    def resolve(pptr: dict[str, Any]) -> str | None:
        file_id = pptr.get("m_FileID", 0)
        if file_id == 0:
            return None
        external = Path(externals[file_id - 1].path).name
        if external != "resources.assets":
            return None
        return guid_by_path_id.get(pptr.get("m_PathID"))

    def requirement(req: dict[str, Any]) -> dict[str, Any] | None:
        quest = resolve(req.get("quest", {}))
        if not quest:
            return None
        return {
            "type": "QuestOutcomeRequirement",
            "quest": quest,
            "comparison": req.get("comparison", 0),
            "outcome": req.get("outcome", 2),
        }

    return {
        "name": tree.get("m_Name"),
        "main": [g for g in map(resolve, tree.get("quests", [])) if g],
        "side": [g for g in map(resolve, tree.get("sideQuests", [])) if g],
        "unlocks": {
            "gifting": requirement(tree.get("giftingUnlockRequirement", {})),
            "nonCommission": requirement(tree.get("nonCommissionRequirement", {})),
            "pigeon": requirement(tree.get("pigeonUnlockRequirement", {})),
        },
    }


# ── C# metadata ───────────────────────────────────────────────────────────
_SIGNED = {0x04, 0x06, 0x08, 0x0A}


def read_assembly_info(dll: Path) -> tuple[dict[str, dict[str, int]], dict[str, list[str]]]:
    """Enum members and base classes of every type in a .NET assembly.

    Enums are keyed ``Name`` (or ``Outer/Name`` when nested). Bases map a class
    name to its ancestors, nearest first.
    """
    import dnfile

    pe = dnfile.dnPE(str(dll))
    try:
        md = pe.net.mdtables
        constants: dict[int, int] = {}
        for row in md.Constant or []:
            parent = row.Parent
            if parent.table is not md.Field:
                continue
            raw = row.Value.value
            if row.Type in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B):
                constants[parent.row_index] = int.from_bytes(
                    raw, "little", signed=row.Type in _SIGNED
                )

        enclosing: dict[int, int] = {}
        for row in md.NestedClass or []:
            enclosing[row.NestedClass.row_index] = row.EnclosingClass.row_index

        typedefs = list(md.TypeDef)

        def key(index: int) -> str:
            name = str(typedefs[index - 1].TypeName)
            outer = enclosing.get(index)
            return f"{key(outer)}/{name}" if outer else name

        enums: dict[str, dict[str, int]] = {}
        parents: dict[str, str] = {}
        for index, row in enumerate(typedefs, start=1):
            # Generic bases are TypeSpec rows, which have no name; none of the
            # types we care about derive from one.
            base_row = row.Extends.row if row.Extends else None
            base = str(getattr(base_row, "TypeName", "") or "") if base_row is not None else ""
            if base == "Enum":
                members = {}
                for field in row.FieldList:
                    name = str(field.row.Name)
                    if name != "value__" and field.row_index in constants:
                        members[name] = constants[field.row_index]
                enums[key(index)] = members
            elif base:
                parents[str(row.TypeName)] = base
    finally:
        pe.close()

    bases: dict[str, list[str]] = {}
    for name in parents:
        chain, current = [], parents.get(name)
        while current and current not in chain:
            chain.append(current)
            current = parents.get(current)
        bases[name] = chain
    return enums, bases
