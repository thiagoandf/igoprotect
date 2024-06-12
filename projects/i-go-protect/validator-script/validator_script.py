"""Validator script for:
    - monitoring subscribed delegators and generating/depositing keys,
    - issuing breach warnings, and 
    - terminating contracts.
"""
import time
import logging
import argparse
import configparser
from pathlib import Path
from typing import List, Tuple

from algokit_utils.beta.algorand_client import AlgorandClient
from algokit_utils.beta.account_manager import AddressAndSigner
from algokit_utils.network_clients import AlgoClientConfig, AlgoClientConfigs
from algosdk import mnemonic, account
from algosdk.encoding import encode_address
from algosdk.atomic_transaction_composer import AccountTransactionSigner

from Locksmith import Locksmith, PartkeyFetcherGoal
from Bouncer import Bouncer
from utils import get_del_app_list, get_val_app_state
from NoticeboardClient import NoticeboardClient


#Delegator smart contract wrappers, for abstracting the interface with the smart contract in case property names change.
def are_part_keys_confirmed(del_app_state):
    return bool(del_app_state.keys_confirmed)
def are_part_keys_deposited(del_app_state):
    return bool(del_app_state.part_keys_deposited)


def check_del_app_state_and_generate_cluster_stack(
    del_app_list: List[dict]
) -> tuple:
    """Separates the delegator apps into active, deposited, and created.

    Args:
        del_app_list (List[dict]): Delegator apps.

    Returns:
        tuple: Active, deposited, and created delegator apps.
    """
    del_app_active_list = []
    del_app_deposited_list = []
    del_app_created_list = []
    for del_app in del_app_list:
        if are_part_keys_confirmed(del_app['state']):
            del_app_active_list.append( del_app )
        elif are_part_keys_deposited(del_app['state']):
            del_app_deposited_list.append( del_app )
        else: 
            del_app_created_list.append( del_app )
    return del_app_active_list, del_app_deposited_list, del_app_created_list



def try_to_go_to_sleep(
    loop_period_s: int, 
    start_time: float
) -> float:
    """Sleep for given amount of time, if it hasn't elapsed yet.

    Args:
        loop_period_s (int): Amount of seconds to sleep.
        start_time (float): System time in seconds.

    Returns:
        float: Amount of time slept.
    """
    elapsed_time_s = time.time() - start_time
    if elapsed_time_s < loop_period_s:
        time.sleep(loop_period_s - elapsed_time_s)
        return loop_period_s - elapsed_time_s
    else: 
        return 0.0



def main(
    # config_path: str = str(Path( Path(__file__).parent, 'default.config' )),
    # log_path: str = str(Path(Path(__file__).parent, 'validator_script.log'))
    config_path: str = './default.config',
    log_path: str = './validator_script.log'
) -> None:
    """Continuously run the validator script on the node.

    Args:
        config_path (str, optional): Path to the config file, including filename. Defaults to `./default.config`.
        log_path (str, optional): Path to the log file, including filename. Defaults to `./validator_script.log`.
    """

    ### Setup ##########################################################################################################
    
    # Create a logger object
    logger = logging.getLogger('main_logger')
    logger.setLevel(logging.INFO) 

    # Create file handler which logs messages
    fh = logging.FileHandler(Path(log_path))
    fh.setLevel(logging.INFO)

    # Create formatter and add it to the handlers
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)

    # Add the handlers to the logger
    logger.addHandler(fh)

    [logger.info('#'*120) for i in range(3)]
    logger.info(f'Started validator script. ' + '#'*94)


    ### Fetch config ###

    config_path = Path(config_path)
    # config_path = Path( Path(__file__).parent, 'default.config' )
    config = configparser.RawConfigParser(defaults=None, strict=False, allow_no_value=True)
    config.read(config_path)
    
    val_app_id = str(config.get('igoprotect_config', 'validator_ad_id'))
    manager_mnemonic_str = str(config.get('igoprotect_config', 'manager_mnemonic'))
    # noticeboard_id = str(config.get('igoprotect_config', 'noticeboard_id'))
    use_algokit = bool(config.get('igoprotect_config', 'use_algokit'))

    algod_config_server =   str(config.get('algo_client_config', 'algod_config_server'))
    algod_config_token =    str(config.get('algo_client_config', 'algod_config_token'))
    indexer_config_server = str(config.get('algo_client_config', 'indexer_config_server'))
    indexer_config_token =  str(config.get('algo_client_config', 'indexer_config_token'))
    kmd_config_server =     str(config.get('algo_client_config', 'kmd_config_server'))
    kmd_config_token =      str(config.get('algo_client_config', 'kmd_config_token'))

    loop_period_s = int(config.get('node_config', 'sleep_time_s'))    


    logger.info(f'Serving validator ad with ID {val_app_id}.')
    logger.info(f'Indexer server configured to {indexer_config_server}')


    ### Configure client ###

    algod_config = AlgoClientConfig(
        server=algod_config_server, 
        token=algod_config_token
    )
    indexer_config = AlgoClientConfig(
        server=indexer_config_server, 
        token=indexer_config_token
    )
    kmd_config = AlgoClientConfig(
        server=kmd_config_server, 
        token=kmd_config_token
    )
    # algorand_client = AlgorandClient.default_local_net()
    algorand_client = AlgorandClient(
        AlgoClientConfigs(
            algod_config=algod_config,
            indexer_config=indexer_config,
            kmd_config=kmd_config,
        )
    )
    algorand_client.set_suggested_params_timeout(0)


    ### Configure manager ###
    
    manager_private_key = mnemonic.to_private_key(manager_mnemonic_str)
    manager_address = account.address_from_private_key(manager_private_key)
    manager = AddressAndSigner(
        address=manager_address,
        signer=AccountTransactionSigner(manager_private_key)
    )
    algorand_client.set_signer(sender=manager.address, signer=manager.signer)


    ### Initialize noticeboard client

    val_app_state = get_val_app_state(
        algorand_client.client.algod,
        val_app_id
    )

    noticeboard_client = NoticeboardClient(
        algod_client=algorand_client.client.algod,
        app_id=val_app_state.noticeboard_app_id
    )


    ### Initialize components

    suggested_params = algorand_client.client.algod.suggested_params()
    suggested_params.fee = 3 * suggested_params.min_fee

    locksmith = Locksmith(
        PartkeyFetcherGoal(use_algokit),
        suggested_params,
        use_algokit
    )

    bouncer = Bouncer( suggested_params )


    while True:

        start_time = time.time()

        logger.info(f"Started new validator loop.")


        ### Fetch delegator contracts ##################################################################################
        
        ### Fetch delegator contracts, associated with this validator ###

        del_app_list = get_del_app_list( 
            algorand_client.client.algod, 
            val_app_id 
        )

        ### Sort delegator contracts into cluster stacks ###
        
        del_app_active_list, del_app_deposited_list, del_app_created_list = \
            check_del_app_state_and_generate_cluster_stack( del_app_list )

        logger.info(f'The following number of delegator contracts was found ({len(del_app_list)} in total): ' +
            f'{len(del_app_active_list)} active, ' +
            f'{len(del_app_deposited_list)} deposited, and ' +
            f'{len(del_app_created_list)} created.'
        )
        
        del del_app_list # No longer needed


        ### Process delegator contracts ################################################################################

        ### Generate and submit participation keys ###

        # Iterate over freshly-created delegator apps (awaiting key generation and submission)
        for del_app in del_app_created_list:

            # Make new key
            partkey = locksmith.generate_partkey(        
                encode_address(del_app['state'].del_acc.as_bytes),
                del_app['state'].round_start,
                del_app['state'].round_end
            )

            # Deposit the key
            result = locksmith.deposit_partkey(
                partkey,
                noticeboard_client,
                encode_address(del_app['state'].del_acc.as_bytes),
                # abi.AddressType().decode(del_app['state'].del_acc.as_bytes),
                manager,
                del_app['id'],
                val_app_id
            )

            logger.info(f"Generated and deposited keys for delegator app with ID {del_app['id']}")

            assert(result)



        ### Check delegator app validity (two steps / loops) and delete if needed ###

        # Iterate over delegator apps with validator-deposited keys (awaiting delegator confirmation)
        for del_app in del_app_deposited_list:
            confirmation_time_elapsed = Bouncer.has_del_app_partkey_confirmation_time_elapsed(
                algorand_client.client.algod.status()['last-round'],
                del_app['state']
            )
            if confirmation_time_elapsed:
                logger.info(f"Partkeys not deposited on time for delegator app with ID {del_app['id']}")
                locksmith.delete_del_app_partkey(
                    encode_address(del_app['state'].del_acc.as_bytes)
                )
                bouncer.end_del_app_due_to_unconfirmed_keys(
                    encode_address(del_app['state'].del_acc.as_bytes),
                    del_app['id'],
                    val_app_id,
                    manager,
                    noticeboard_client
                )
                logger.info(f"Deleted partkeys and terminate unconfirmed delegator app with ID {del_app['id']}")
        

        # Iterate over delegator apps with delegator-confrimed keys (may expire or breach terms)
        for del_app in del_app_active_list:

            ### Check for expiry
            has_del_app_expired = Bouncer.has_del_app_expired(
                algorand_client.client.algod.status()['last-round'],
                del_app['state']
            )
            if has_del_app_expired:
                logger.info(f"Detected contract expiry for delegator app with ID {del_app['id']}")
                bouncer.end_del_app_due_to_expiry(
                    encode_address(del_app['state'].del_acc.as_bytes),
                    del_app['id'],
                    val_app_id,
                    manager,
                    noticeboard_client
                )
                logger.info(f"Terminated expired delegator app with ID {del_app['id']}")
                try: 
                    locksmith.delete_del_app_partkey( del_app['state'].del_acc )
                except:
                    logger.info('Tried deleting non-existent partkeys (expected for expired delegator app).')

            ### Check for term breach
            has_del_indefinitely_breached_terms = Bouncer.has_del_indefinitely_breached_terms(
                algorand_client.client.algod.status()['last-round'],
                del_app['state']
            )
            if has_del_indefinitely_breached_terms:
                locksmith.delete_del_app_partkey(del_app['state'].del_acc)
                Bouncer.terminate_del_app(
                    encode_address(del_app['state'].del_acc.as_bytes),
                    noticeboard_client
                )
                logger.info(f"Deleted keys and terminated delegator app with ID {del_app['id']}")

        slept = try_to_go_to_sleep(loop_period_s, start_time)

        logger.info(f'Waking up after sleeping {round(slept, 1)} seconds')
    


if __name__ == '__main__':

    repo_link = 'https://github.com'
    parser = argparse.ArgumentParser(description=f"IgoProtec validator script. For a guide, see {repo_link}.")
    parser.add_argument(
        '--config_path', type=str, help='Path to the config file', 
        required=False, default=Path( Path(__file__).parent, 'default.config' )
    )
    parser.add_argument(
        '--log_path', type=str, help='Path to the log file', 
        required=False, default=Path( Path(__file__).parent, 'validator_script.log' )
    )
    args = parser.parse_args()

    main(args.config_path, args.log_path)
