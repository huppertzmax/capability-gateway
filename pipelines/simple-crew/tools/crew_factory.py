"""
Factory helpers to assemble CrewAI agents and tasks from declarative configs.
"""
from typing import Any, Callable, Dict, List, Optional
from crewai import Agent, Crew, LLM, Task
from crewai.a2a import A2AConfig
from crewai.process import Process
from crewai.mcp import MCPServerHTTP
from shared.run_context import RUN_ID_HEADER

def _build_mcp_config(mcp_urls: List[str], run_id: Optional[str]) -> List[MCPServerHTTP]:
    """
    Build a list of MCPServerHTTP instances from a list of tool names.
    """
    mcp_config = []
    for mcp_server_url in mcp_urls:
        headers = {RUN_ID_HEADER: run_id} if run_id else None
        mcp = MCPServerHTTP(
            url=mcp_server_url,
            headers=headers,
            cache_tools_list=False,
        )
        mcp_config.append(mcp)
    return mcp_config

def _build_agent(
    spec: dict,
    model: LLM,
    mcp_config: Optional[List[str]],
    run_id: Optional[str],
    a2a_config: Optional[List[A2AConfig]],
    step_callback: Callable[[Any], None] | None,
) -> Agent:
    """
    Build a CrewAI Agent from a spec dictionary.
    """
    agent_kwargs = {
        'role': spec['role'],
        'goal': spec['goal'],
        'backstory': spec['backstory'],
        'llm': model,
        'mcps': _build_mcp_config(mcp_config, run_id) if (mcp_config and spec.get('mcp', False)) else [],
        'allow_delegation': False,
        'verbose': True,
        'max_iter': 6
    }
    if spec.get('a2a', False):
        if a2a_config is None:
            raise ValueError('Agent spec requests a2a but no a2a_config provided')
        agent_kwargs['a2a'] = a2a_config
    if step_callback is not None:
        agent_kwargs['step_callback'] = step_callback
    return Agent(**agent_kwargs)


def _build_task(spec: dict, agents: Dict[str, Agent], run_id: Optional[str]) -> Task:
    """
    Build a CrewAI Task from a spec dictionary.
    """
    agent_id = spec['agent']
    if agent_id not in agents:
        raise ValueError(f"Task references unknown agent '{agent_id}'")
    task_config = dict(spec.get('config', {}))
    if run_id:
        metadata = dict(task_config.get('metadata', {}))
        metadata['run_id'] = run_id
        task_config['metadata'] = metadata
    task_kwargs = {
        'description': spec['description'],
        'expected_output': spec['expected_output'],
        'agent': agents[agent_id],
        'config': task_config,
    }
    return Task(**task_kwargs)


def build_crew_from_config(
    config: dict,
    model: LLM,
    mcp_config: Optional[List[str]] = None,
    run_id: Optional[str] = None,
    a2a_config: Optional[List[A2AConfig]] = None,
    step_callback: Callable[[Any], None] | None = None,
) -> Crew:
    """
    Build a CrewAI Crew from a normalized config.
    Config schema:
    {
        "name": "<crew name>",
        "agents": [{ "id": "...", "role": "...", "goal": "...", "backstory": "...", "a2a": true|false }],
        "tasks": [{ "name": "...", "agent": "agent_id", "description": "...", "expected_output": "..." }]
    }
    """
    agents: Dict[str, Agent] = {}
    for agent_spec in config['agents']:
        agent = _build_agent(
            agent_spec,
            model=model,
            mcp_config=mcp_config,
            run_id=run_id,
            a2a_config=a2a_config,
            step_callback=step_callback,
        )
        agents[agent_spec['id']] = agent
    tasks = [
        _build_task(task_spec, agents=agents, run_id=run_id)
        for task_spec in config['tasks']
    ]
    process_value = config.get('process', 'sequential')
    process = process_value if isinstance(process_value, Process) else Process.sequential
    return Crew(
        name=config.get('name', 'Crew'),
        agents=list(agents.values()),
        tasks=tasks,
        process=process,
        verbose=True,
        tracing=config.get('tracing', False),
    )
