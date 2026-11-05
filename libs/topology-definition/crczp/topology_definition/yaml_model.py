"""
Pydantic models loaded from YAML.

A YamlModel loads the way the yamlize objects it replaces did: ruamel's round-trip parser reads
the document (YAML 1.2, merge keys), unknown keys are rejected, a value converts to a field's
type only when the conversion keeps it equal, and an error names the place in the document.
"""

from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import IO, Annotated, Any, Self, get_args, get_origin

from pydantic import BaseModel, ConfigDict, PlainValidator, ValidationError
from pydantic.fields import FieldInfo
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from ruamel.yaml.scalarbool import ScalarBoolean

MERGE_TAG = 'tag:yaml.org,2002:merge'
NULL_TAG = 'tag:yaml.org,2002:null'
STR_TAG = 'tag:yaml.org,2002:str'


class YamlModelError(ValueError):
    """
    A YAML document does not describe a valid model. When the place is known, the message ends
    with where it starts and ends in the document.
    """

    def __init__(self, msg: str, node: Node | None = None) -> None:
        if node is not None:
            msg = f'{msg}\nstart: {node.start_mark}\nend: {node.end_mark}'
        super().__init__(msg)


def _converted(type_: type) -> PlainValidator:
    """
    Accept a value of type_, or one that converts to type_ without changing its value.
    """

    def validate(value: Any) -> Any:
        # bool subclasses int and True == 1, so a YAML boolean would pass as a number.
        if type_ is int and isinstance(value, bool):
            raise ValueError(f'Expected an integer, got the boolean `{value}`.')
        if isinstance(value, type_):
            return value
        try:
            new_value = type_(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f'Failed to coerce data `{value}` to type `{type_}`') from exc
        if new_value != value:
            raise ValueError(
                f'Coerced `{type(value)}` to `{type(new_value)}`, but the new value '
                f'`{new_value}` is not equal to old `{value}`.'
            )
        return new_value

    return PlainValidator(validate)


YamlStr = Annotated[str, _converted(str)]
YamlInt = Annotated[int, _converted(int)]
YamlBool = Annotated[bool, _converted(bool)]


def positional_init[F: Callable[..., None]](init: F) -> F:
    """
    Mark a model constructor that only passes positional arguments on as fields.

    Pydantic calls a model's own __init__ while validating it, unless the function carries
    the marker its BaseModel.__init__ has. Validation passes fields by keyword, so loading
    skips such a constructor and reports a missing key instead of a missing argument.
    """
    setattr(init, '__pydantic_base_init__', True)  # noqa: B010  # a dunder pydantic reads
    return init


class YamlModel(BaseModel):
    """
    A model loaded from and dumped to YAML.

    Instances compare and hash by identity, as the yamlize objects they replace did, so they
    serve as set members and dict keys.
    """

    model_config = ConfigDict(extra='forbid', validate_assignment=True)

    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return object.__hash__(self)

    @classmethod
    def load(cls, stream: str | IO[str]) -> Self:
        """
        Load an instance from a YAML document.

        :raises YamlModelError: When the document is not valid YAML or not a valid instance.
        """
        yaml = YAML(typ='rt')
        try:
            root = yaml.compose(stream)
            if not isinstance(root, MappingNode):
                raise YamlModelError('Expected a mapping node', root)
            cls._prepare(root, set())
            data = _plain(yaml.constructor.construct_document(root))
        except YAMLError as exc:
            raise YamlModelError(str(exc)) from exc
        try:
            model = cls.model_validate(data)
        except ValidationError as exc:
            raise _error(cls, exc, root) from exc
        model._after_load_tree(root)  # pylint: disable=protected-access  # an instance of cls
        return model

    @classmethod
    def from_file(cls, path: str | Path) -> Self:
        """
        Load an instance from a YAML file.
        """
        with open(path, encoding='utf-8') as file:
            return cls.load(file)

    @classmethod
    def dump(cls, data: Self) -> str:
        """
        Dump an instance to YAML, writing only the fields it was loaded or built with.
        """
        stream = StringIO()
        dumped = data.model_dump(mode='json', exclude_unset=True, by_alias=True)
        YAML(typ='rt').dump(dumped, stream)
        return stream.getvalue()

    @classmethod
    def _fix_node(cls, node: MappingNode) -> None:
        """
        Adjust the YAML mapping of an instance before it is constructed. Nothing by default.
        """
        del node

    def _after_load(self, node: MappingNode) -> None:
        """
        Check or complete what only a loaded instance needs, raising YamlModelError at its node.
        Nothing by default; nested instances run this before the one holding them.
        """
        del node

    @classmethod
    def _prepare(cls, node: MappingNode, seen: set[int]) -> None:
        """
        Run _fix_node on this mapping and on the mappings of the instances it holds.

        Merge keys are resolved first, so the checks also see the entries they bring in.
        """
        if id(node) in seen:
            return
        seen.add(id(node))
        _resolve_merges(node)
        cls._fix_node(node)
        for key_node, child in node.value:
            key = key_node.value
            field = _field_for_key(cls, key) if isinstance(key, str) else None
            if field is None:
                continue
            for model in _models_in(field.annotation):
                items = child.value if isinstance(child, SequenceNode) else [child]
                for item in items:
                    if isinstance(item, MappingNode):
                        # pylint: disable-next=protected-access  # a nested YamlModel class
                        model._prepare(item, seen)

    def _after_load_tree(self, node: MappingNode) -> None:
        """
        Run _after_load on the instances this one holds, then on this one.
        """
        for name, field in type(self).model_fields.items():
            value = getattr(self, name)
            child = find_value_node(node, field.alias or name)
            if child is None:
                continue
            if isinstance(value, list) and isinstance(child, SequenceNode):
                pairs = list(zip(value, child.value, strict=False))
            else:
                pairs = [(value, child)]
            for item, item_node in pairs:
                if isinstance(item, YamlModel) and isinstance(item_node, MappingNode):
                    item._after_load_tree(item_node)  # pylint: disable=protected-access
        self._after_load(node)


def _resolve_merges(mapping: MappingNode) -> None:
    """
    Replace a mapping's merge keys with the entries they bring in. A key the mapping sets
    itself wins, then the earlier merged mapping. Merged keys are copies, so that renaming one
    leaves the anchored mapping alone. A merge of anything but mappings is left for ruamel to
    reject.
    """
    merges = [value for key, value in mapping.value if key.tag == MERGE_TAG]
    parents = [
        p for merge in merges for p in (merge.value if isinstance(merge, SequenceNode) else [merge])
    ]
    if not merges or not all(isinstance(parent, MappingNode) for parent in parents):
        return
    entries = [(key, value) for key, value in mapping.value if key.tag != MERGE_TAG]
    keys = {key.value for key, _ in entries}
    for parent in parents:
        _resolve_merges(parent)
        for key, value in parent.value:
            if key.value not in keys:
                keys.add(key.value)
                copied = ScalarNode(
                    key.tag, key.value, key.start_mark, key.end_mark, style=key.style
                )
                entries.append((copied, value))
    mapping.value = entries


def find_value_node(mapping: MappingNode, key: str) -> Node | None:
    """
    The node of key's value in a mapping, following merge keys; None when the key is absent.
    """
    merged: list[Node] = []
    for key_node, child in mapping.value:
        if key_node.tag == MERGE_TAG:
            merged += child.value if isinstance(child, SequenceNode) else [child]
        elif key_node.value == key:
            return child
    for parent in merged:
        if isinstance(parent, MappingNode) and (found := find_value_node(parent, key)):
            return found
    return None


def value_node(mapping: MappingNode, key: str) -> Node:
    """
    The node of key's value in a mapping, or the mapping itself when the key is absent.
    """
    return find_value_node(mapping, key) or mapping


def _field_for_key(model: type[YamlModel], key: str) -> FieldInfo | None:
    """
    The field a YAML key sets: the one aliased to the key, else the one named so.
    """
    return next(
        (field for name, field in model.model_fields.items() if (field.alias or name) == key),
        None,
    )


def _models_in(annotation: Any) -> list[type[YamlModel]]:
    """
    The YamlModel classes an annotation holds, directly or as items of a list or dict.
    """
    if get_origin(annotation) is None and isinstance(annotation, type):
        return [annotation] if issubclass(annotation, YamlModel) else []
    return [model for arg in get_args(annotation) for model in _models_in(arg)]


def _plain(value: Any) -> Any:
    """
    Copy constructed YAML data into plain dicts, lists, str, int, float and bool.
    """
    if isinstance(value, dict):
        return {_plain(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, ScalarBoolean):
        return bool(value)
    for type_ in (bool, int, float, str):
        if isinstance(value, type_):
            return type_(value)
    return value


def _node_at(root: Node, loc: tuple[int | str, ...]) -> Node:
    """
    The deepest node of the document along a pydantic error location.
    """
    node = root
    for part in loc:
        child: Node | None = None
        if isinstance(node, MappingNode) and isinstance(part, str):
            child = find_value_node(node, part)
        elif isinstance(node, SequenceNode) and isinstance(part, int) and part < len(node.value):
            child = node.value[part]
        if child is None:
            break
        node = child
    return node


def _model_at(model: type[YamlModel], loc: tuple[int | str, ...]) -> type[YamlModel]:
    """
    The model class an error location ends in.
    """
    for part in loc:
        field = _field_for_key(model, part) if isinstance(part, str) else None
        if field is not None and (models := _models_in(field.annotation)):
            model = models[0]
    return model


def _error(model: type[YamlModel], exc: ValidationError, root: MappingNode) -> YamlModelError:
    """
    A YamlModelError for the first problem pydantic found, at its place in the document.
    """
    errors = exc.errors(include_url=False)
    loc = tuple(errors[0]['loc'])
    kind = errors[0]['type']
    if kind == 'missing':
        owner_loc = loc[:-1]
        names = [
            error['loc'][-1]
            for error in errors
            if error['type'] == 'missing' and tuple(error['loc'][:-1]) == owner_loc
        ]
        owner = _model_at(model, owner_loc).__name__
        return YamlModelError(
            f'Missing {owner} attributes without default: {names}', _node_at(root, owner_loc)
        )
    if kind == 'extra_forbidden':
        owner_node = _node_at(root, loc[:-1])
        key_node = next(
            (key for key, _ in getattr(owner_node, 'value', []) if key.value == loc[-1]),
            owner_node,
        )
        owner = _model_at(model, loc[:-1])
        keys = [field.alias or name for name, field in owner.model_fields.items()]
        return YamlModelError(
            f'Error parsing {owner.__name__}, found key `{loc[-1]}` but expected any of {keys}',
            key_node,
        )
    node = _node_at(root, loc)
    if kind in ('model_type', 'model_attributes_type', 'dict_type'):
        return YamlModelError('Expected a mapping node', node)
    if kind == 'list_type':
        return YamlModelError('Expected a SequenceNode', node)
    if kind == 'value_error':
        return YamlModelError(str(errors[0].get('ctx', {}).get('error')), node)
    return YamlModelError(errors[0]['msg'], node)
