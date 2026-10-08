from gateway.gateway.config.config import AclPolicy, CallerAclPolicy
from gateway.gateway.services.capability_schemas import Capability
from fastapi import HTTPException

class AccessController:
    def __init__(self, acl_policy: AclPolicy):
        """
        Initialize the access controller with the given ACL policy.
        Args:
            acl_policy: The access control policy to enforce.
        """
        self.acl_policy = acl_policy

    def get_caller_permissions(self, caller_agent_id: str) -> CallerAclPolicy:
        """
        Retrieve the permissions for the given caller agent.
        Args:
            caller_agent_id: ID of the calling agent.
        Returns:
            CallerAclPolicy: The permissions for the caller, or raises HTTPException if not allowed.
        """
        caller_policy = self.acl_policy.get_policy(caller_agent_id)
        if caller_policy is None:
            raise HTTPException(
                status_code=403,
                detail="Caller is not allowed",
            )
        return caller_policy

    def is_agent_allowed(self, caller_agent_id: str, capability: Capability) -> bool:
        """
        Check if the caller agent is allowed to access the given capability.
        Args:
            caller_agent_id: ID of the calling agent.
            capability: The capability to check access for.
        Returns:
            bool: True if access is allowed, False otherwise.
        """
        caller_policy = self.get_caller_permissions(caller_agent_id)
        if capability.kind == "tool":
            return capability.capability_name in caller_policy.allow_tools
        elif capability.kind == "agent":
            return capability.source_name in caller_policy.allow_agents
        return False