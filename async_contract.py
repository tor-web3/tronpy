from typing import Union, Optional, Any, Tuple
from Crypto.Hash import keccak

from wallet.tronpy.exceptions import DoubleSpending
from wallet.tronpy.abi import trx_abi
from wallet.tronpy import keys
from wallet import tronpy


def keccak256(data: bytes) -> bytes:
    hasher = keccak.new(digest_bits=256)
    hasher.update(data)
    return hasher.digest()


def assure_bytes(value: Union[str, bytes]) -> bytes:
    if isinstance(value, (str,)):
        return bytes.fromhex(value)
    if isinstance(value, (bytes,)):
        return value
    raise ValueError("bad bytes format")


# noinspection PyProtectedMember
class AsyncContract(object):
    """A smart contract object."""

    def __init__(
        self,
        addr=None,
        *,
        bytecode: Union[str, bytes] = '',
        name: str = None,
        abi: Optional[dict] = None,
        user_resource_percent: int = 100,
        origin_energy_limit: int = 1,
        origin_address: str = None,
        owner_address: str = "410000000000000000000000000000000000000000",
        client: "tronpy.AsyncTron" = None,
    ):
        self.contract_address = addr
        """Address of the contract"""

        self._bytecode = assure_bytes(bytecode)

        self.name = name
        """Name of the contract"""

        self.abi = abi or []
        """ABI list of the contract"""

        self.user_resource_percent = user_resource_percent
        """User resource percent, default 100"""

        self.origin_energy_limit = origin_energy_limit
        """Origin energy limit, default 1"""

        self.origin_address = origin_address
        """Origin address of the contract, i.e. contract creator"""

        self.owner_address = owner_address
        """Current transaction owner's address, to call or trigger contract"""

        self._functions = None
        self._client = client

    def __str__(self):
        return "<Contract {} {}>".format(self.name, self.contract_address)

    @property
    def bytecode(self):
        """Bytecode of the contract, in ``hex`` format"""
        return self._bytecode.hex()

    @bytecode.setter
    def bytecode(self, value):
        self._bytecode = assure_bytes(value)

    def deploy(self) -> Any:
        if self.contract_address:
            raise RuntimeError("this contract has already deployed to {}".format(self.contract_address))

        if self.origin_address != self.owner_address:
            raise RuntimeError("origin address and owner address mismatch")

        return self._client.trx._build_transaction(
            "CreateSmartContract",
            {
                "owner_address": keys.to_hex_address(self.owner_address),
                "new_contract": {
                    "origin_address": keys.to_hex_address(self.origin_address),
                    "abi": {"entrys": self.abi},
                    "bytecode": self.bytecode,
                    "call_value": 0,  # TODO
                    "name": self.name,
                    "consume_user_resource_percent": self.user_resource_percent,
                    "origin_energy_limit": self.origin_energy_limit,
                },
            },
        )

    def update_user_resource_percent(self, percent: int) -> "tronpy.async_tron.AsyncTransactionBuilder":
        """Create a Transaction to update user resource percent."""
        if self.origin_address != self.owner_address:
            raise RuntimeError("origin address and owner address mismatch")

        return self._client.trx._build_transaction(
            "UpdateSettingContract",
            {
                "owner_address": keys.to_hex_address(self.owner_address),
                "contract_address": keys.to_hex_address(self.contract_address),
                "consume_user_resource_percent": percent,
            },
        )

    def update_origin_energy_limit(self, limit: int) -> "tronpy.async_tron.AsyncTransactionBuilder":
        """Create a Transaction to update origin energy limit."""
        if self.origin_address != self.owner_address:
            raise RuntimeError("origin address and owner address mismatch")

        return self._client.trx._build_transaction(
            "UpdateEnergyLimitContract",
            {
                "owner_address": keys.to_hex_address(self.owner_address),
                "contract_address": keys.to_hex_address(self.contract_address),
                "origin_energy_limit": limit,
            },
        )

    def clear_abi(self) -> "tronpy.async_tron.AsyncTransactionBuilder":
        """Clear contract ABI."""
        if self.origin_address != self.owner_address:
            raise RuntimeError("origin address and owner address mismatch")

        return self._client.trx._build_transaction(
            "ClearAbiContract",
            {
                "owner_address": keys.to_hex_address(self.owner_address),
                "contract_address": keys.to_hex_address(self.contract_address),
            },
        )

    @property
    def functions(self) -> "ContractFunctions":
        """The :class:`~ContractFunctions` object, wraps all contract methods."""
        if self._functions is None:
            if self.abi:
                self._functions = ContractFunctions(self)
                return self._functions
            raise ValueError("can not call a contract without ABI")
        return self._functions

    @property
    def constructor(self) -> "AsyncContractConstructor":
        """The constructor of the contract."""
        for method_abi in self.abi:
            if method_abi['type'] == 'Constructor':
                return AsyncContractConstructor(method_abi, self)

        raise NameError("Contract has no constructor")

    def as_shielded_trc20(self) -> "ShieldedTRC20":
        return ShieldedTRC20(self)


class ContractFunctions(object):
    def __init__(self, contract):
        self._contract = contract

    def __getitem__(self, method: str):
        for method_abi in self._contract.abi:
            if method_abi["type"].lower() == "function" and method_abi["name"] == method:
                return AsyncContractMethod(method_abi, self._contract)

        raise KeyError("contract has no method named '{}'".format(method))

    def __getattr__(self, method: str):
        """Get the actual contract method object."""
        try:
            return self[method]
        except KeyError:
            raise AttributeError("contract has no method named '{}'".format(method))

    def __dir__(self):
        return [method["name"] for method in self._contract.abi if method["type"].lower() == "function"]

    def __iter__(self):
        yield from [self[method] for method in dir(self)]


class AsyncContractConstructor(object):
    """The constructor method of a contract."""

    def __init__(self, abi: dict, contract: AsyncContract):

        self._abi = abi
        self._contract = contract

        self.inputs = abi.get("inputs", [])
        self.outputs = abi.get("outputs", [])

    def __str__(self):
        types = ", ".join(arg["type"] + " " + arg.get("name", "") for arg in self.inputs)
        ret = "construct({})".format(types)
        return ret

    @property
    def input_type(self) -> str:
        return "(" + (",".join(arg["type"] for arg in self.inputs)) + ")"

    def encode_parameter(self, *args, **kwargs) -> str:
        """Encode constructor parameters according to ABI."""
        parameter = ""

        if args and kwargs:
            raise ValueError("do not mix positional arguments and keyword arguments")

        if len(self.inputs) == 0:
            if args or kwargs:
                raise TypeError("{} constructor requires {} arguments".format(self._contract.name, len(self.inputs)))
        elif args:
            if len(args) != len(self.inputs):
                raise TypeError("wrong number of arguments, require {} got {}".format(len(self.inputs), len(args)))
            parameter = trx_abi.encode_single(self.input_type, args).hex()
        elif kwargs:
            if len(kwargs) != len(self.inputs):
                raise TypeError("wrong number of arguments, require {} got {}".format(len(self.inputs), len(args)))
            args = []
            for arg in self.inputs:
                try:
                    args.append(kwargs[arg["name"]])
                except KeyError:
                    raise TypeError("missing argument '{}'".format(arg["name"]))
            parameter = trx_abi.encode_single(self.input_type, args).hex()

        return parameter


# noinspection PyProtectedMember
class AsyncContractMethod(object):
    def __init__(self, abi: dict, contract: AsyncContract):

        self._abi = abi
        self._contract = contract
        self._owner_address = contract.owner_address
        self._client = contract._client

        self.inputs = abi.get("inputs", [])
        self.outputs = abi.get("outputs", [])

        self.call_value = 0
        self.call_token_value = 0
        self.call_token_id = 0

    def __str__(self):
        return self.function_type

    def with_owner(self, addr: str) -> "AsyncContractMethod":
        """Set the calling owner address.

        Can also be changed through
        :meth:`TransactionBuilder.with_owner() <tronpy.async_tron.AsyncTransactionBuilder.with_owner>`.
        """
        self._owner_address = addr
        return self

    def with_transfer(self, amount: int) -> "AsyncContractMethod":
        """Call a contract function with TRX transfer. ``amount`` in `SUN`."""
        self.call_value = amount
        return self

    def with_asset_transfer(self, amount: int, token_id: int) -> "AsyncContractMethod":
        """Call a contract function with TRC10 token transfer."""
        self.call_token_value = amount
        self.call_token_id = token_id
        return self

    async def call(self, *args, **kwargs) -> "tronpy.async_tron.AsyncTransactionBuilder":
        """Call the contract method."""
        return await self.__call__(*args, **kwargs)

    def parse_output(self, raw: str) -> Any:
        """Parse contract result as result."""
        parsed_result = trx_abi.decode_single(self.output_type, bytes.fromhex(raw))
        if len(self.outputs) == 1:
            return parsed_result[0]
        if len(self.outputs) == 0:
            return None
        return parsed_result

    async def __call__(self, *args, **kwargs) -> "tronpy.async_tron.AsyncTransactionBuilder":
        """Call the contract method."""
        parameter = ""

        if args and kwargs:
            raise ValueError("do not mix positional arguments and keyword arguments")

        if len(self.inputs) == 0:
            if args or kwargs:
                raise TypeError("{} expected {} arguments".format(self.name, len(self.inputs)))
        elif args:
            if len(args) != len(self.inputs):
                raise TypeError("wrong number of arguments, require {} got {}".format(len(self.inputs), len(args)))
            parameter = trx_abi.encode_single(self.input_type, args).hex()
        elif kwargs:
            if len(kwargs) != len(self.inputs):
                raise TypeError("wrong number of arguments, require {} got {}".format(len(self.inputs), len(args)))
            args = []
            for arg in self.inputs:
                try:
                    args.append(kwargs[arg["name"]])
                except KeyError:
                    raise TypeError("missing argument '{}'".format(arg["name"]))
            parameter = trx_abi.encode_single(self.input_type, args).hex()
        else:
            raise TypeError("wrong number of arguments, require {}".format(len(self.inputs)))

        if self._abi.get("stateMutability", None).lower() in ["view", "pure"]:
            # const call, contract ret
            ret = await self._client.trigger_const_smart_contract_function(
                self._owner_address, self._contract.contract_address, self.function_signature, parameter,
            )

            return self.parse_output(ret)

        else:
            return self._client.trx._build_transaction(
                "TriggerSmartContract",
                {
                    "owner_address": keys.to_hex_address(self._owner_address),
                    "contract_address": keys.to_hex_address(self._contract.contract_address),
                    "data": self.function_signature_hash + parameter,
                    "call_token_value": self.call_token_value,
                    "call_value": self.call_value,
                    "token_id": self.call_token_id,
                },
                method=self,
            )

    @property
    def name(self) -> str:
        return self._abi["name"]

    @property
    def input_type(self) -> str:
        return "(" + (",".join(self.__format_json_abi_type_entry(arg) for arg in self.inputs)) + ")"

    @property
    def output_type(self) -> str:
        return "({})".format(",".join(self.__format_json_abi_type_entry(arg) for arg in self.outputs))

    def __format_json_abi_type_entry(self, entry) -> str:
        if entry['type'].startswith('tuple'):
            surfix = entry['type'][5:]
            if 'components' not in entry:
                raise ValueError("ABIEncoderV2 used, ABI should be set by hand")
            return "({}){}".format(
                ",".join(self.__format_json_abi_type_entry(arg) for arg in entry['components']), surfix
            )
        else:
            return entry['type']

    @property
    def function_signature(self) -> str:
        return self.name + self.input_type

    @property
    def function_signature_hash(self) -> str:
        return keccak256(self.function_signature.encode())[:4].hex()

    @property
    def function_type(self) -> str:
        types = ", ".join(arg["type"] + " " + arg.get("name", "") for arg in self.inputs)
        ret = "function {}({})".format(self.name, types)
        if self._abi.get("stateMutability", None).lower() == "view":
            ret += " view"
        elif self._abi.get("stateMutability", None).lower() == "pure":
            ret += " pure"
        if self.outputs:
            ret += " returns ({})".format(", ".join(arg["type"] + " " + arg.get("name", "") for arg in self.outputs))
        return ret


# noinspection PyProtectedMember
class ShieldedTRC20(object):
    """Shielded TRC20 Wrapper."""

    def __init__(self, contract: AsyncContract):
        self.shielded = contract
        """Thi shielded TRC20 contract."""

        self._client = contract._client

        # lazy properties
        self._trc20 = None
        self._scale_factor = None

    @property
    async def trc20(self) -> AsyncContract:
        """The corresponding TRC20 contract."""
        if self._trc20 is None:
            trc20_address = "41" + self.shielded._bytecode[-52:-32].hex()
            self._trc20 = self._client.get_contract(trc20_address)
        return await self._trc20

    @property
    def scale_factor(self) -> int:
        """Scaling factor of the shielded contract."""
        if self._scale_factor is None:
            self._scale_factor = self.shielded.functions.scalingFactor()
        return self._scale_factor

    async def get_rcm(self) -> str:
        return (await self._client.provider.make_request("wallet/getrcm"))["value"]

    async def mint(
        self, taddr: str, zaddr: str, amount: int, memo: str = ""
    ) -> "tronpy.async_tron.AsyncTransactionBuilder":
        """Mint, transfer from T-address to z-address."""
        rcm = await self.get_rcm()
        payload = {
            "from_amount": str(amount),
            "shielded_receives": {
                "note": {
                    "value": amount // self.scale_factor,
                    "payment_address": zaddr,
                    "rcm": rcm,
                    "memo": memo.encode().hex(),
                }
            },
            "shielded_TRC20_contract_address": keys.to_hex_address(self.shielded.contract_address),
        }

        ret = await self._client.provider.make_request("wallet/createshieldedcontractparameters", payload)
        self._client._handle_api_error(ret)
        parameter = ret["trigger_contract_input"]
        function_signature = self.shielded.functions.mint.function_signature_hash
        return self._client.trx._build_transaction(
            "TriggerSmartContract",
            {
                "owner_address": keys.to_hex_address(taddr),
                "contract_address": keys.to_hex_address(self.shielded.contract_address),
                "data": function_signature + parameter,
            },
            method=self.shielded.functions.mint,
        )

    async def transfer(
        self, zkey: dict, notes: Union[list, dict], *to: Union[Tuple[str, int], Tuple[str, int, str]],
    ) -> "tronpy.async_tron.AsyncTransactionBuilder":
        """Transfer from z-address to z-address."""
        if isinstance(notes, (dict,)):
            notes = [notes]

        assert 1 <= len(notes) <= 2

        spends = []
        spend_amount = 0
        for note in notes:
            if note.get("is_spent", False):
                raise DoubleSpending
            alpha = await self.get_rcm()
            root, path = self.get_path(note.get("position", 0))
            spends.append(
                {"note": note["note"], "alpha": alpha, "root": root, "path": path, "pos": note.get("position", 0)}
            )
            spend_amount += note["note"]["value"]

        receives = []
        receive_amount = 0
        for recv in to:
            addr = recv[0]
            amount = recv[1]
            receive_amount += amount
            if len(recv) == 3:
                memo = recv[2]
            else:
                memo = ""

            rcm = await self.get_rcm()

            receives.append(
                {"note": {"value": amount, "payment_address": addr, "rcm": rcm, "memo": memo.encode().hex()}}
            )

        if spend_amount != receive_amount:
            raise ValueError("spend amount is not equal to receive amount")

        payload = {
            "ask": zkey["ask"],
            "nsk": zkey["nsk"],
            "ovk": zkey["ovk"],
            "shielded_spends": spends,
            "shielded_receives": receives,
            "shielded_TRC20_contract_address": keys.to_hex_address(self.shielded.contract_address),
        }
        ret = await self._client.provider.make_request("wallet/createshieldedcontractparameters", payload)
        self._client._handle_api_error(ret)
        parameter = ret["trigger_contract_input"]
        function_signature = self.shielded.functions.transfer.function_signature_hash
        return self._client.trx._build_transaction(
            "TriggerSmartContract",
            {
                "owner_address": "0000000000000000000000000000000000000000",
                "contract_address": keys.to_hex_address(self.shielded.contract_address),
                "data": function_signature + parameter,
            },
            method=self.shielded.functions.transfer,
        )

    async def burn(
        self, zkey: dict, note: dict, *to: Union[Tuple[str, int], Tuple[str, int, str]]
    ) -> "tronpy.async_tron.AsyncTransactionBuilder":
        """Burn, transfer from z-address to T-address."""
        spends = []
        alpha = await self.get_rcm()
        root, path = self.get_path(note.get("position", 0))
        if note.get("is_spent", False):
            raise DoubleSpending
        spends.append(
            {"note": note["note"], "alpha": alpha, "root": root, "path": path, "pos": note.get("position", 0)}
        )
        change_amount = 0
        receives = []
        to_addr = None
        to_amount = 0
        to_memo = ''
        if not to:
            raise ValueError('burn must have a output')
        for receive in to:
            addr = receive[0]
            amount = receive[1]
            if len(receive) == 3:
                memo = receive[2]
            else:
                memo = ""

            if addr.startswith('ztron1'):
                change_amount += amount
                rcm = await self.get_rcm()
                receives = [
                    {"note": {"value": amount, "payment_address": addr, "rcm": rcm, "memo": memo.encode().hex()}}
                ]
            else:
                # assume T-address
                to_addr = addr
                to_amount = amount
                to_memo = memo

        if note["note"]["value"] * self.scale_factor - change_amount * self.scale_factor != to_amount:
            raise ValueError("Balance amount is wrong")

        payload = {
            "ask": zkey["ask"],
            "nsk": zkey["nsk"],
            "ovk": zkey["ovk"],
            "shielded_spends": spends,
            "shielded_receives": receives,
            "to_amount": str(to_amount),
            "transparent_to_address": keys.to_hex_address(to_addr),
            "shielded_TRC20_contract_address": keys.to_hex_address(self.shielded.contract_address),
        }

        ret = await self._client.provider.make_request("wallet/createshieldedcontractparameters", payload)
        self._client._handle_api_error(ret)
        parameter = ret["trigger_contract_input"]
        function_signature = self.shielded.functions.burn.function_signature_hash
        txn = self._client.trx._build_transaction(
            "TriggerSmartContract",
            {
                "owner_address": "410000000000000000000000000000000000000000",
                "contract_address": keys.to_hex_address(self.shielded.contract_address),
                "data": function_signature + parameter,
            },
            method=self.shielded.functions.burn,
        )
        if to_memo:
            txn = txn.memo(to_memo)
        return txn

    def _fix_notes(self, notes: list) -> list:
        for note in notes:
            if "position" not in note:
                note["position"] = 0
            if "is_spent" not in note:
                note["is_spent"] = False
            # if "memo" in note["note"]:
            #     note["note"]["memo"] = bytes.fromhex(note["note"]["memo"]).decode("utf8", 'ignore')
        return notes

    # use zkey pair from wallet/getnewshieldedaddress
    async def scan_incoming_notes(self, zkey: dict, start_block_number: int, end_block_number: int = None) -> list:
        """Scan incoming notes using ivk, ak, nk."""
        if end_block_number is None:
            end_block_number = start_block_number + 1000
        payload = {
            "start_block_index": start_block_number,
            "end_block_index": end_block_number,
            "shielded_TRC20_contract_address": keys.to_hex_address(self.shielded.contract_address),
            "ivk": zkey["ivk"],
            "ak": zkey["ak"],
            "nk": zkey["nk"],
        }
        ret = await self._client.provider.make_request("wallet/scanshieldedtrc20notesbyivk", payload)
        self._client._handle_api_error(ret)
        return self._fix_notes(ret.get("noteTxs", []))

    async def scan_outgoing_notes(
        self, zkey_or_ovk: Union[dict, str], start_block_number: int, end_block_number: int = None
    ) -> list:
        """Scan outgoing notes using ovk."""
        if end_block_number is None:
            end_block_number = start_block_number + 1000

        ovk = zkey_or_ovk
        if isinstance(zkey_or_ovk, (dict,)):
            ovk = zkey_or_ovk["ovk"]

        payload = {
            "start_block_index": start_block_number,
            "end_block_index": end_block_number,
            "shielded_TRC20_contract_address": keys.to_hex_address(self.shielded.contract_address),
            "ovk": ovk,
        }
        ret = await self._client.provider.make_request("wallet/scanshieldedtrc20notesbyovk", payload)
        self._client._handle_api_error(ret)
        return ret.get("noteTxs", [])

    # (root, path)
    def get_path(self, position: int = 0) -> (str, str):
        root, path = self.shielded.functions.getPath(position)
        root = root.hex()
        path = "".join(p.hex() for p in path)
        return root, path

    async def is_note_spent(self, zkey: dict, note: dict) -> bool:
        """Is a note spent."""
        payload = dict(note)
        payload["shielded_TRC20_contract_address"] = keys.to_hex_address(self.shielded.contract_address)
        if "position" not in note:
            payload["position"] = 0
        payload["ak"] = zkey["ak"]
        payload["nk"] = zkey["nk"]

        ret = await self._client.provider.make_request("wallet/isshieldedtrc20contractnotespent", payload)

        return ret.get('is_spent', None)
