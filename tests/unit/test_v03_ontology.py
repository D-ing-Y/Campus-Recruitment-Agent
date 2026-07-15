from campus_job_agent.ontology import CapabilityOntology


def test_ontology_resolves_alias_and_preserves_unknown() -> None:
    ontology = CapabilityOntology.load_default()
    assert ontology.resolve("大语言模型").capability_id == "ai.llm"
    unknown = ontology.resolve("A Brand New Skill")
    assert not unknown.matched
    assert unknown.raw_label == "A Brand New Skill"
    assert unknown.capability_id is None
