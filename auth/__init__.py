# encoding:utf-8
"""RongAI multi-tenant identity & access management service.

The package owns ``identity.db`` and exposes the domain service, store, session
and policy primitives needed by the web console's four admin views and by the
runtime resource-isolation layer. See ``openspec/changes/add-tenant-identity-
access-management`` for the authoritative behaviour contract.
"""
