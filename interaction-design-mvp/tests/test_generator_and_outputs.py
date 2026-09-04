from __future__ import annotations

from evedesign.model import Generator
from evedesign.system import SystemInstance

from interaction_design.conversion import spec_to_system
from interaction_design.generator import ODesignGenerator
from interaction_design.runtime import MockODesignExecutor
from interaction_design.specs import load_spec


def test_mock_executor_satisfies_evedesign_generator_contract(tmp_path):
    spec = load_spec("examples/ligand_binder.json")
    system = spec_to_system(spec)
    generator = ODesignGenerator(MockODesignExecutor(), tmp_path).build(system, spec)

    assert isinstance(generator, Generator)
    instances = generator.generate(3, entities=[1], temperature=0.8)

    assert len(instances) == 3
    assert all(isinstance(instance, SystemInstance) for instance in instances)
    assert all(len(instance) == len(system) for instance in instances)
    assert all(instance[1].rep is not None for instance in instances)
    assert all(instance[1].models is not None for instance in instances)
    assert all("af3_summary" in instance.metadata["artifacts"] for instance in instances)
    assert generator.last_run_dir is not None
    assert (generator.last_run_dir / "odesign_input.json").is_file()


def test_fixed_pos_fails_with_actionable_domain_mapping_message(tmp_path):
    spec = load_spec("examples/ligand_binder.json")
    generator = ODesignGenerator(MockODesignExecutor(), tmp_path).build(spec_to_system(spec), spec)
    try:
        generator.generate(1, entities=[1], fixed_pos={1: [3]})
    except ValueError as error:
        assert "fixed segments" in str(error)
    else:
        raise AssertionError("fixed_pos must not be silently ignored")
