import logging
import subprocess
from typing import Tuple, List
from math import sqrt
from dataclasses import dataclass
import base64

import numpy as np
import pandas as pd
from algosdk.transaction import SuggestedParams
from algosdk.encoding import encode_address
from algosdk.atomic_transaction_composer import AccountTransactionSigner
from algokit_utils import TransactionParameters
from algokit_utils.beta.algorand_client import AlgorandClient
from algokit_utils.beta.account_manager import AddressAndSigner

from NoticeboardClient import NoticeboardClient



@dataclass
class ParticipationKey:
    sel_key: str            # str, from `goal account partkeyinfo`
    vote_key: str           # str, from `goal account partkeyinfo`
    state_proof_key: str    # str, from `goal account partkeyinfo`
    vote_key_dilution: int  # int(round(Sqrt(duration)))
    round_start: int        # Take from delegator contract
    round_end: int          # Take from delegator contract


 
def run_cmd_command_and_wait_for_output(
    command_args: List[str]
) -> Tuple[bool, str]:
    """Run a command in the command line, wait for its output, and capture the output.

    Args:
        command_args (List[str]): Strings of individual words that make up the command.

    Returns:
        Tuple[bool, str]: Command validity (execution successful = 0) and the captured STDOUT.

    """
    command_validity = False
    result = None
    try:
        result = subprocess.run(command_args, capture_output=True, text=True)
        if result.returncode == 0:
            command_validity = True
        elif result.returncode < 0:
            logging.warning(f"`{' '.join(command_args)}` returned code {result.returncode} and {result.stdout}.")
        else:
            logging.warning(f"`{' '.join(command_args)}` returned error `{result.stderr}`.")
    except OSError as e:
        logging.warning(f"Calling `{' '.join(command_args)}` raised error {e}.")
    return command_validity, result.stdout



class PartkeyFetcher(object):
    
    def __init__(
            self
        ) -> None:
        """Initialize the interface for fetching participation key details.
        """
        pass

    def get_partkey_details(
        self, 
        partkey_id: int
    ) -> ParticipationKey:
        """Get the participation key details.

        Args:
            partkey_id (int): ID of the partkey.

        Raises:
            ValueError: No keys found for ID.

        Returns:
            ParticipationKey: Participation key details.
        """
        pass



class PartkeyFetcherGoal(PartkeyFetcher):

    # Comman for retrieveing partkey details
    COMMAND_INFO_WITH_ALGOKIT = ["algokit", "goal", "account", "partkeyinfo"]
    COMMAND_INFO_GOAL_ONLY = ["goal", "account", "partkeyinfo"]

    # Comman for retrieveing a list partkeys (used to verifying the main, detailed command)
    COMMAND_LIST_WITH_ALGOKIT = ["algokit", "goal", "account", "listpartkeys"]
    COMMAND_LIST_GOAL_ONLY = ["goal", "account", "listpartkeys"]

    # Columns used in the participation key table (parameters of each partkey)
    COLUMNS = dict(
        participation_id='Participation ID',
        parent_address='Parent address',
        last_vote_round='Last vote round',
        last_block_proposal_round='Last block proposal round',
        effective_first_round='Effective first round',
        effective_last_round='Effective last round',
        first_round='First round',
        last_round='Last round',
        key_dilution='Key dilution',
        selection_key='Selection key',
        voting_key='Voting key',
        state_proof_key='State proof key'
    )


    def __init__(
        self,
        use_algokit: bool = True
    ) -> None:
        """Initialize the interface for fetching participation keys, based on the `algokit goal` command.

        Args:
            use_algokit (bool): Flag, indicating whether to use `goal` through `algokit` or standalone.
        """
        if use_algokit:
            self.COMMAND_INFO = self.COMMAND_INFO_WITH_ALGOKIT
            self.COMMAND_LIST = self.COMMAND_LIST_WITH_ALGOKIT
        else:
            self.COMMAND_INFO = self.COMMAND_INFO_GOAL_ONLY
            self.COMMAND_LIST = self.COMMAND_LIST_GOAL_ONLY
        self.partkey_table = None
        self.refresh_partkey_table()


    def get_partkey_details(
        self, 
        partkey_id: int,
        refresh_table: bool = True
    ) -> ParticipationKey:
        """Get the participation key details.

        Args:
            partkey_id (int): ID of the partkey.
            refresh_table (bool): Flag, indicating whether to first refresh the internal partkey table. Default is `True`.

        Raises:
            ValueError: No keys found for ID.
            RuntimeError: Multiple keys found.

        Returns:
            ParticipationKey: Participation key details.
        """
        if refresh_table:
            self.refresh_partkey_table()
        row = self.partkey_table.query(f'participation_id == "{partkey_id}"')
        num_of_keys = row.shape[0]
        if num_of_keys == 0:
            raise ValueError(f'No keys found for ID {partkey_id}')
        elif num_of_keys > 1:
            raise RuntimeError(f'Multiple keys found ({num_of_keys})')
        partkey = ParticipationKey(
            sel_key=row['selection_key'].values[0],
            vote_key=row['voting_key'].values[0],
            state_proof_key=row['state_proof_key'].values[0],
            vote_key_dilution=int(row['key_dilution'].values[0]),
            round_start=int(row['first_round'].values[0]),
            round_end=int(row['last_round'].values[0])
        )
        return partkey
    

    def get_partkey_id_from_acc(
        self,
        acc: str
    ) -> str:
        row = self.partkey_table.query(f'parent_address == "{acc}"')
        if len(row) == 0:
            raise ValueError(f'No partkeys found for account ID {acc}')
        elif len(row) > 1:
            raise ValueError(f'More than one parkey found for account ID {acc}')
        return row['participation_id'].values[0]


    def refresh_partkey_table(
        self
    ) -> pd.DataFrame:
        """Retrieve participation keys and updated the internal partkey table.

        Notes:
            Issues two blocking system calls to `algokit` in order to fetch the participation key information.

        Raises:
            RuntimeError: List or/and info command invalid.

        Returns:
            pd.DataFrame: Table of participation keys.
        """
        list_cmd_validity, list_cmd_result = run_cmd_command_and_wait_for_output(
            self.COMMAND_LIST
        )
        info_cmd_validity, info_cmd_result = run_cmd_command_and_wait_for_output(
            self.COMMAND_INFO
        )
        if list_cmd_validity and info_cmd_validity:
            # Keep the worker function separate for easier testing
            self.partkey_table = self._make_partkey_table_from_stdout(
                list_cmd_result, 
                info_cmd_result
            )
            return self.partkey_table
        else:
            raise RuntimeError(f'List or/and info command invalid ({list_cmd_validity} and {info_cmd_validity})')


    def get_partkey_table(
            self
        ) -> pd.DataFrame:
        """Retrieve the internal participation key table.

        Returns:
            pd.DataFrame: Table of participation keys.
        """
        return self.partkey_table


    def _make_partkey_table_from_stdout(
            self, 
            list_cmd_result: str, 
            info_cmd_result: str
        ) -> pd.DataFrame:
        """Get the participation keys from the `partkeyinfo` STDOUT.

        Args:
            list_cmd_result (str): STDOUT from calling `listpartkeys`.
            info_cmd_result (str): STDOUT from calling `partkeyinfo`.

        Returns:
            pd.DataFrame: Table of participation keys.
        """
        # Get a reference number of keys for verifying master the output's validity
        num_of_keys = len(list_cmd_result.split('\n')) - 2  # Subtract header row and trailing new line
        partkey_list_raw = self._filter_partkeys_from_stdout(info_cmd_result)
        if len(partkey_list_raw) != num_of_keys:
            logging.warning(
                f'Number of keys from list {num_of_keys} and info {len(partkey_list_raw)} command do not match.'
            )
            return None
        partkey_table = self._convert_partkey_list_raw_to_table(partkey_list_raw)
        return partkey_table



    def _filter_partkeys_from_stdout(
            self, 
            info_cmd_result: str
        ) -> List[List[str]]:
        """Generate a list, containing a nested list of lines associated with an individual partkey.

        Args:
            info_cmd_result (str): STDOUT from calling `partkeyinfo`.

        Returns:
            List[List[str]]: Nested list of lines associated with an individual partkey.
        """
        # Convert string to list
        res = info_cmd_result.split('\n')[1:]  # Drop header

        # Get the start/end line indexes, separating individual partkeys
        delimiter_idx = np.array([], dtype=int)
        for i, r in enumerate(res):
            if r == ' ':
                delimiter_idx = np.r_[delimiter_idx, i]
        delimiter_idx = np.r_[delimiter_idx, len(res)-1]

        # Group the lines of a partkey
        partkey_list_raw = []
        for i in range(delimiter_idx.size - 1):
            start_idx = delimiter_idx[i] + 1
            end_idx = delimiter_idx[i + 1]
            partkey_list_raw.append(res[start_idx:end_idx])

        if self._check_partkey_list_raw_format_validity(partkey_list_raw):
            return partkey_list_raw
        else:
            logging.warning('Partkey format does not seem valid.')
            return None


    def _check_partkey_list_raw_format_validity(
            self, 
            partkey_list_raw: List[List[str]]
        ) -> bool:
        """Check the number of lines and the names (and order) of the partkey data, obtained via STDOUT.

        Args:
            partkey_list_raw (List[List[str]]): Nested list of lines associated with an individual partkey.

        Returns:
            bool: Indicator whether valid.
        """
        for partkey in partkey_list_raw:
            if len(partkey) != 12:
                return False
            for line, col_val in zip(partkey, self.COLUMNS.values()):
                if line[1:len(col_val)+1] != col_val:
                    return False
        return True


    def _convert_partkey_list_raw_to_table(
            self, 
            partkey_list_raw: List[List[str]]
        ) -> pd.DataFrame:
        """Convert the nested list of partkey info to a table.

        Args:
            partkey_list_raw (List[List[str]]): Nested list of lines associated with an individual partkey.

        Returns:
            pd.DataFrame: Table of participation keys.
        """
        partkey_table = pd.DataFrame(columns=(self.COLUMNS), index=[*range(len(partkey_list_raw))])
        for i, partkey in enumerate(partkey_list_raw):
            # df = pd.DataFrame(columns=list(self.COLUMNS), index=[i])
            for line in partkey:
                key, value = line.split(':')
                key = key.strip()   # Remove leading (and trailing spaces)
                value = value.strip()   # Remove leading (and trailing spaces)
                column = list(self.COLUMNS.keys())[np.squeeze(np.where( np.array(list(self.COLUMNS.values())) == key ))]
                partkey_table.loc[i, column] = value
            # partkey_table = pd.concat([partkey_table, df])
        return partkey_table



class Locksmith(object):


    def __init__(self, 
        partkey_fetcher: PartkeyFetcher, 
        suggested_params: SuggestedParams,
        use_algokit: bool = True
    ) -> None:
        self.part_key_fetcher = partkey_fetcher
        self.suggested_params = suggested_params
        self.use_algokit = use_algokit


    def generate_partkey(
        self: object,
        del_acc: str,
        round_start: int,
        round_end: int
    ) -> ParticipationKey:
        command_args = self._addpartkey_cmd_command_args( del_acc, round_start, round_end )
        valid, result = run_cmd_command_and_wait_for_output(command_args)
        if not valid:
            raise RuntimeError(f'Invalid command call {command_args.join(" ")}')
        partkey_id = self._get_partkey_id(result)   
        self.part_key_fetcher.refresh_partkey_table()
        partkey = self.part_key_fetcher.get_partkey_details(partkey_id)
        return partkey


    def deposit_partkey(
        self,
        partkey: ParticipationKey,
        noticeboard_client: NoticeboardClient,
        del_acc: str,
        manager: AddressAndSigner,
        del_app_id: int,
        val_app_id: int
    ) -> int:
        result = noticeboard_client.deposit_keys(
            del_acc=del_acc,
            sel_key=base64.b64decode(partkey.sel_key),
            vote_key=base64.b64decode(partkey.vote_key),
            state_proof_key=base64.b64decode(partkey.state_proof_key),
            vote_key_dilution=round(sqrt(partkey.vote_key_dilution)),
            round_start=partkey.round_start,
            round_end=partkey.round_end,
            transaction_parameters=TransactionParameters(
                sender=manager.address,
                signer=manager.signer,
                foreign_apps=[val_app_id, del_app_id],
                accounts=[del_acc],
                suggested_params=self.suggested_params,
            ),
        )
        return result.confirmed_round


    def _addpartkey_cmd_command_args(
        self,
        del_acc: str, 
        first_valid: int, 
        last_valid: int
    ) -> str:
        dilution = int(round(sqrt(last_valid - last_valid)))
        command = ['goal', 'account', 'addpartkey', 
                f'-a={del_acc}',
                f'--roundFirstValid={first_valid}',
                f'--roundLastValid={last_valid}',
                f'--keyDilution={dilution}']
        if self.use_algokit:
            return ['algokit'] + command
        else:
            return command


    def _deletepartkey_cmd_command_args(
        self,
        partkey_id: str
    ) -> str:
        command = ['goal', 'account', 'deletepartkey', f'--partkeyid={partkey_id}']
        if self.use_algokit:
            return ['algokit'] + command
        else:
            return command


    def _get_partkey_id(
        self,
        cmd_command_return: int
    ) -> str:
        target_str = 'Participation ID: '
        start_index = cmd_command_return.split('\n')[1].find(target_str) + len(target_str)
        partkey_id = cmd_command_return.split('\n')[1][start_index:]
        return partkey_id


    def delete_del_app_partkey(
            self,
            del_acc: str
        ) -> tuple:
            """Delete participation key, generated for a delegator application.

            Args:
                del_acc (str): Delegator account address with checksum and base32 encoded.

            Returns:
                tuple: Command validity and stdout.
            """
            partkey_id = self.part_key_fetcher.get_partkey_id_from_acc( del_acc )
            command_args = self._deletepartkey_cmd_command_args( partkey_id )
            return run_cmd_command_and_wait_for_output( command_args )



if __name__ == '__main__':

    ### Manual testing ###
    # Notes:
    # - Make sure the manager for signing and the manager that made the validator app match

    from utils import get_del_state


    ### Set up Algorand client
    algorand_client = AlgorandClient.default_local_net()
    algorand_client.set_suggested_params_timeout(0)


    ### Set up manager
    manager_address='V63CU7LIOOJ53LWK7V5EAUZ3F5Y3F737AGJJSXUSZJVPMAGAJFVLJZOWCY'
    manager_signer=AccountTransactionSigner(
        'ds9eqoqfoswV9NVi4LLTiNPfjZ6M6NuraVrCg1/8AKmvtip9aHOT3a7K/XpAUzsvcbL/fwGSmV6Symr2AMBJag=='
    )
    manager = AddressAndSigner(
        address=manager_address,
        signer=manager_signer
    )
    algorand_client.set_signer(
        sender=manager.address, 
        signer=manager.signer
    )


    ### Set up delegator
    # Note: the validator app ID would be known to the validator script (part of config)
    del_app_id = 1086 # Hardcoded in this case, later fetched from validator app
    del_app_state = get_del_state(
        algorand_client.client.algod,
        del_app_id
    )
    val_app_id = del_app_state.val_app_id   # Can also fetch from delegator


    ### Set up noticeboard
    # Note: all deposits go through the noticeboard client
    noticeboard_client = NoticeboardClient(
        algod_client=algorand_client.client.algod,
        app_id=del_app_state.noticeboard_app_id,    # Fetch noticeboard ID from del
    )


    ### The tested component
    suggested_params = algorand_client.client.algod.suggested_params()
    suggested_params.fee = 3 * suggested_params.min_fee
    locksmith = Locksmith(
        PartkeyFetcherGoal(),
        suggested_params
    )

    # Make new key
    partkey = locksmith.generate_partkey(        
        encode_address(del_app_state.del_acc.as_bytes),
        del_app_state.round_start,
        del_app_state.round_end
    )

    # Deposit the keys
    result = locksmith.deposit_partkey(
        partkey,
        noticeboard_client,
        encode_address(del_app_state.del_acc.as_bytes),
        # abi.AddressType().decode(del_app_state.del_acc.as_bytes),
        manager,
        del_app_id,
        val_app_id
    )

    print(result)

    pass
