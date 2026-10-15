from pipez import llm

FORBIDDEN = {"minItems", "maxItems", "minimum", "maximum",
             "minLength", "maxLength", "pattern", "format"}


def _walk(node, path=""):
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, path
        for k, v in node.items():
            if k != "properties":
                assert k not in FORBIDDEN, "%s/%s" % (path, k)
            if k == "properties":
                for pk, pv in v.items():
                    _walk(pv, "%s/%s" % (path, pk))
            else:
                _walk(v, "%s/%s" % (path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, "%s[%d]" % (path, i))


def test_schemas_strict_safe():
    for name, s in [("decision", llm.decision_schema()),
                    ("intake", llm.INTAKE_SCHEMA), ("brief", llm.BRIEF_SCHEMA),
                    ("plan", llm.plan_schema()), ("result", llm.RESULT_SCHEMA),
                    ("verdicts", llm.VERDICTS_SCHEMA)]:
        _walk(s, name)
