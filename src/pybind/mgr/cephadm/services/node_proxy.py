import json

from typing import List, Any, Dict, Tuple

from .cephadmservice import CephadmDaemonDeploySpec, CephService
from orchestrator import OrchestratorError


class NodeProxy(CephService):
    TYPE = 'node-proxy'

    def prepare_create(self, daemon_spec: CephadmDaemonDeploySpec) -> CephadmDaemonDeploySpec:
        assert self.TYPE == daemon_spec.daemon_type
        daemon_id, host = daemon_spec.daemon_id, daemon_spec.host

        if not self.mgr.http_server.agent:
            raise OrchestratorError('Cannot deploy node-proxy before creating cephadm endpoint')

        keyring = self.get_keyring_with_caps(self.get_auth_entity(daemon_id, host=host), [])
        daemon_spec.keyring = keyring

        daemon_spec.final_config, daemon_spec.deps = self.generate_config(daemon_spec)

        return daemon_spec

    def generate_config(self, daemon_spec: CephadmDaemonDeploySpec) -> Tuple[Dict[str, Any], List[str]]:
        # node-proxy is re-using the agent endpoint and therefore
        # needs similar checks to see if the endpoint is ready.
        agent_endpoint = self.mgr.http_server.agent
        try:
            assert agent_endpoint
            assert agent_endpoint.ssl_certs.get_root_cert()
            assert agent_endpoint.server_port
        except Exception:
            raise OrchestratorError(
                'Cannot deploy node-proxy daemons until cephadm endpoint has finished generating certs')

        listener_cert, listener_key = agent_endpoint.ssl_certs.generate_cert(daemon_spec.host, self.mgr.inventory.get_addr(daemon_spec.host))
        cfg = {
            'target_ip': self.mgr.get_mgr_ip(),
            'target_port': agent_endpoint.server_port,
            'name': f'node-proxy.{daemon_spec.host}',
            'keyring': daemon_spec.keyring,
            'root_cert.pem': agent_endpoint.ssl_certs.get_root_cert(),
        }
        config = {'node-proxy.json': json.dumps(cfg)}

        return config, sorted([str(self.mgr.get_mgr_ip()), str(agent_endpoint.server_port),
                               agent_endpoint.ssl_certs.get_root_cert()])
