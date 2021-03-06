from vFense.core.agent import *
import logging
import logging.config
from vFense import VFENSE_LOGGING_CONFIG

from vFense.utils.common import *
from vFense.core.agent.agents import update_agent_field, get_agent_info
from vFense.core.tag.tagManager import get_tags_by_agent_id, delete_agent_from_all_tags
from vFense.core.tag.tagManager import delete_agent_from_all_tags
from vFense.core.tag import *
from vFense.db.client import db_create_close, r
from vFense.plugins.patching._constants import CommonAppKeys
from vFense.plugins.patching import *
from vFense.plugins.patching.patching import remove_all_app_data_for_agent, \
    update_all_app_data_for_agent

from vFense.plugins.patching._db_stats import  get_all_app_stats_by_agentid
from vFense.errorz.error_messages import GenericResults
from vFense.server.hierarchy import Collection
import redis
from rq import Queue

rq_host = 'localhost'
rq_port = 6379
rq_db = 0
rq_pool = redis.StrictRedis(host=rq_host, port=rq_port, db=rq_db)

logging.config.fileConfig(VFENSE_LOGGING_CONFIG)
logger = logging.getLogger('rvapi')


class AgentController():
    def __init__(self, agent_id, user_name=None, uri=None, method=None):
        self.agent_id = agent_id
        self.user_name = user_name
        self.uri = uri
        self.method = method

    @db_create_close
    def get_data(self):
        try:
            agent_data = get_agent_info(agent_id)
            agent_data[AgentKey.LastAgentUpdate] = (
                int(agent_data[AgentKey.LastAgentUpdate].strftime('%s'))
            )
            if agent_data:
                agent_data['tags'] = get_tags_by_agent_id(agent_id=self.agent_id)
                agent_data[CommonAppKeys.BASIC_RV_STATS] = (
                    get_all_app_stats_by_agentid(self.agent_id)
                )
                status = (
                    GenericResults(
                        self.username, uri, method
                        ).information_retrieved(agent_data, 1)
                )
                logger.info(status['message'])
            else:
                status = (
                    GenericResults(
                        self.username, uri, method
                        ).invalid_id(self.agent_id, 'agent_id')
                )
                logger.info(status['message'])

        except Exception as e:
            agent_data = None
            status = (
                GenericResults(
                    self.username, uri, method
                ).something_broke(self.agent_id, 'agents', e)
            )
            logger.error(status['message'])

        return(status)

    @db_create_close
    def get_common_fields(self, fields, uri=None, method=None, conn=None):
        try:
            if isinstance(fields, list):
                agent_data = (
                    r
                    .table(AgentsCollection)
                    .get(self.agent_id)
                    .pluck(fields)
                    .run(conn)
                )
                if agent_data:
                    status = (
                        GenericResults(
                            self.username, uri, method
                            ).information_retrieved(agent_data, 1)
                    )
                    logger.info(status['message'])
                else:
                    status = (
                        GenericResults(
                            self.username, uri, method
                            ).invalid_id(self.agent_id, 'agent_id')
                    )
                    logger.info(status['message'])

        except Exception as e:
            status = (
                GenericResults(
                    self.username, uri, method
                ).something_broke(self.agent_id, 'agents', e)
            )
            logger.error(status['message'])

        return(status)

    @db_create_close
    def get_tags(self, uri=None, method=None, conn=None):
        try:
            tags = get_tags_by_agent_id(agent_id=self.agent_id)
            if tags:
                for i in range(len(tags)):
                    name = tags[i].pop('tag_name')
                    tags[i]['name'] = name

                    tag_id = tags[i].pop('tag_id')
                    tags[i]['id'] = tag_id

            status = (
                GenericResults(
                    self.username, uri, method
                ).information_retrieved(tags, 1)
            )
            logger.info(status['message'])

        except Exception as e:
            status = (
                GenericResults(
                    self.username, uri, method
                ).something_broke(self.agent_id, 'agent', e)
            )
            logger.error(status['message'])

        return(status)


    def displayname_changer(self, displayname, uri=None, method=None):
        results = (
            self._changer(
                displayname, AgentKey.DisplayName,
                uri, method)
        )

        return(results)

    def computername_changer(self, computername, uri=None, method=None):
        results = (
            self._changer(
                computername, AgentKey.ComputerName,
                uri, method)
        )

        return(results)

    def hostname_changer(self, hostname, uri=None, method=None):
        results = (
            self._changer(
                hostname, AgentKey.HostName,
                uri, method)
        )

        return(results)

    def production_state_changer(self, prod_state, uri=None, method=None):
        results = (
            self._changer(
                prod_state, AgentKey.ProductionLevel,
                uri, method)
        )

        return(results)

    def _changer(self, newname, name_type=AgentKey.DisplayName,
                 uri=None, method=None):
        if newname:
            agent_updated = (
                update_agent_field(
                    self.agent_id, name_type, newname, self.username,
                    uri, method
                )
            )

        return(agent_updated)

    def update_fields(self, data, uri=None, method=None):
        if isinstance(data, dict):
            agent_updated = (
                update_agent_fields(
                    self.agent_id, data, newname, self.username,
                    uri, method
                )
            )

        return(agent_updated)

    @db_create_close
    def delete_agent(self, uri, method, conn=None):
        try:
            agent_info = get_agent_info(self.agent_id)
            if agent_info:
                (
                    r
                    .table(AgentsCollection)
                    .get(self.agent_id)
                    .delete()
                    .run(conn)
                )

                (
                    r
                    .table(HardwarePerAgentCollection)
                    .get_all(self.agent_id, index=HardwarePerAgentIndexes.AgentId)
                    .delete()
                    .run(conn)
                )
                (
                    r
                    .table(TagsPerAgentCollection)
                    .get_all(self.agent_id, index=TagsPerAgentIndexes.AgentId)
                    .delete()
                    .run(conn)
                )

                rv_q = Queue('delete_agent', connection=rq_pool)
                rv_q.enqueue_call(
                    func=remove_all_app_data_for_agent,
                    args=(self.agent_id,),
                    timeout=3600,
                )
                status = (
                    GenericResults(
                        self.username, uri, method
                    ).object_deleted(self.agent_id, 'agents')
                )
                logger.info(status['message'])

            else:
                status = (
                    GenericResults(
                        self.username, uri, method
                    ).invalid_id(self.agent_id, 'agents')
                )
                logger.info(status['message'])

        except Exception as e:
            status = (
                GenericResults(
                    self.username, uri, method
                ).something_broke(self.agent_id, 'agents', e)
            )
            logger.exception(status['message'])

        return(status)

    @db_create_close
    def change_customer(self, customer_name, uri, method, conn=None):
        try:
            cexists = (
                r
                .table(Collection.Customers)
                .get(customer_name)
                .run(conn)
            )
            customer_data = {AgentKey.CustomerName: customer_name}
            if cexists:
                update_agent_field(
                    self.agent_id,
                    AgentKey.CustomerName,
                    customer_name,
                    self.username,
                    uri, method
                )
                delete_agent_from_all_tags(self.agent_id)
                rv_q = Queue('move_agent', connection=rq_pool)
                rv_q.enqueue_call(
                    func=update_all_app_data_for_agent,
                    args=(self.agent_id, customer_data),
                    timeout=3600,
                )
                status = (
                    GenericResults(
                        self.username, uri, method
                    ).object_updated(self.agent_id, 'agent', customer_data)
                )
            else:
                status = (
                    GenericResults(
                        self.username, uri, method
                    ).invalid_id(self.agent_id, 'agent')
                )

        except Exception as e:
            status = (
                GenericResults(
                    self.username, uri, method
                ).something_broke(self.agent_id, 'agents', e)
            )
            logger.exception(e)
        
        return(status)

