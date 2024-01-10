from threading import Thread
from redfishdellsystem import RedfishDellSystem
from reporter import Reporter
from util import Config, Logger, http_req
from typing import Dict, Any, Optional
import traceback
import logging
import ssl
import json

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    'reporter': {
        'check_interval': 5,
        'push_data_max_retries': 30,
        'endpoint': 'https://127.0.0.1:7150/node-proxy/data',
    },
    'system': {
        'refresh_interval': 5
    },
    'server': {
        'port': 8080,
    },
    'logging': {
        'level': 20,
    }
}


class NodeProxyManager(Thread):
    def __init__(self,
                 mgr_host: str,
                 cephx_name: str,
                 cephx_secret: str,
                 ca_path: str,
                 mgr_agent_port: int = 7150):
        super().__init__()
        self.mgr_host = mgr_host
        self.cephx_name = cephx_name
        self.cephx_secret = cephx_secret
        self.ca_path = ca_path
        self.mgr_agent_port = str(mgr_agent_port)
        self.stop = False
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = True
        self.ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        self.ssl_ctx.load_verify_locations(self.ca_path)

    def run(self) -> None:
        self.init()
        self.loop()

    def init(self) -> None:
        node_proxy_meta = {
            'cephx': {
                'name': self.cephx_name,
                'secret': self.cephx_secret
            }
        }
        status, result = http_req(hostname=self.mgr_host,
                                  port=self.mgr_agent_port,
                                  data=json.dumps(node_proxy_meta),
                                  endpoint='/node-proxy/oob',
                                  ssl_ctx=self.ssl_ctx)
        if status != 200:
            msg = f'No out of band tool details could be loaded: {status}, {result}'
            logger.debug(msg)
            raise RuntimeError(msg)

        result_json = json.loads(result)
        kwargs = {
            'host': result_json['result']['addr'],
            'username': result_json['result']['username'],
            'password': result_json['result']['password'],
            'cephx': node_proxy_meta['cephx'],
            'mgr_host': self.mgr_host,
            'mgr_agent_port': self.mgr_agent_port
        }
        if result_json['result'].get('port'):
            kwargs['port'] = result_json['result']['port']

        self.node_proxy: NodeProxy = NodeProxy(**kwargs)
        self.node_proxy.start()

    def loop(self) -> None:
        while not self.stop:
            try:
                status = self.node_proxy.check_status()
                label = 'Ok' if status else 'Critical'
                logger.debug(f'node-proxy status: {label}')
            except Exception as e:
                logger.error(f'node-proxy not running: {e.__class__.__name__}: {e}')
                time.sleep(120)
                self.init()
            else:
                logger.debug('node-proxy alive, next check in 60sec.')
                time.sleep(60)

    def shutdown(self) -> None:
        self.stop = True
        # if `self.node_proxy.shutdown()` is called before self.start(), it will fail.
        if self.__dict__.get('node_proxy'):
            self.node_proxy.shutdown()


class NodeProxy(Thread):
    def __init__(self, **kw: Any) -> None:
        super().__init__()
        self.username: str = kw.get('username', '')
        self.password: str = kw.get('password', '')
        self.host: str = kw.get('host', '')
        self.port: int = kw.get('port', 443)
        self.cephx: Dict[str, Any] = kw.get('cephx', {})
        self.reporter_scheme: str = kw.get('reporter_scheme', 'https')
        self.mgr_host: str = kw.get('mgr_host', '')
        self.mgr_agent_port: str = kw.get('mgr_agent_port', '')
        self.reporter_endpoint: str = kw.get('reporter_endpoint', '/node-proxy/data')
        self.exc: Optional[Exception] = None
        self.log = Logger(__name__)

    def run(self) -> None:
        try:
            self.main()
        except Exception as e:
            self.exc = e
            return

    def shutdown(self) -> None:
        self.log.logger.info('Shutting down node-proxy...')
        self.system.client.logout()
        self.system.stop_update_loop()
        self.reporter_agent.stop()

    def check_auth(self, realm: str, username: str, password: str) -> bool:
        return self.username == username and \
            self.password == password

    def check_status(self) -> bool:
        if self.__dict__.get('system') and not self.system.run:
            raise RuntimeError('node-proxy encountered an error.')
        if self.exc:
            traceback.print_tb(self.exc.__traceback__)
            self.log.logger.error(f'{self.exc.__class__.__name__}: {self.exc}')
            raise self.exc
        return True

    def main(self) -> None:
        # TODO: add a check and fail if host/username/password/data aren't passed
        self.config = Config('/etc/ceph/node-proxy.yml', default_config=DEFAULT_CONFIG)
        self.log = Logger(__name__, level=self.config.__dict__['logging']['level'])

        # create the redfish system and the obsever
        self.log.logger.info('Server initialization...')
        try:
            self.system = RedfishDellSystem(host=self.host,
                                            port=self.port,
                                            username=self.username,
                                            password=self.password,
                                            config=self.config)
        except RuntimeError:
            self.log.logger.error("Can't initialize the redfish system.")
            raise

        try:
            self.reporter_agent = Reporter(self.system,
                                           self.cephx,
                                           reporter_scheme=self.reporter_scheme,
                                           reporter_hostname=self.mgr_host,
                                           reporter_port=self.mgr_agent_port,
                                           reporter_endpoint=self.reporter_endpoint)
            self.reporter_agent.run()
        except RuntimeError:
            self.log.logger.error("Can't initialize the reporter.")
            raise

node_proxy_mgr = NodeProxyManager('10.10.10.11',
                                  'agent.whatever',
                                  'AQCzax1lY5H8FRAAMSj2xFsS3CFCBFsoIozqmg==',
                                  7150)
if not node_proxy_mgr.is_alive():
    node_proxy_mgr.start()

# if node_proxy_mgr.is_alive():
#     node_proxy_mgr.shutdown()