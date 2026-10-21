"""Standard and protocol rules that turn calls into asset effects.

Every rule records *why* it matched (``rule`` id and ``rule_note``) and checks
the implementation or interface it relies on, not only the function name:

* ``oz.erc20.internal`` — OpenZeppelin ERC20 ``_mint`` / ``_burn`` /
  ``_transfer``: the declaring contract must come from a configured
  OpenZeppelin dependency and the implementation must write the balance and
  supply state variables.
* ``structural.token-accounting`` — any internal function that updates a
  supply scalar and the balance mapping read by ``balanceOf`` in the same
  direction (mint / burn) or two balance entries in opposite directions
  (transfer). Other address-keyed amounts are ledgers (``structural.ledger``,
  see :mod:`fluxscope.summaries` / :mod:`fluxscope.semantics`).
* ``oz.safeerc20`` — OpenZeppelin SafeERC20 wrappers whose body encodes the
  corresponding ERC-20 call.
* ``safetransferlib`` — solmate / solady ``SafeTransferLib`` wrappers
  (``safeTransfer`` / ``safeTransferFrom`` / ``safeApprove``) whose assembly
  body encodes the ERC-20 function selector.
* ``erc20.call`` — ERC-20 ``transfer`` / ``transferFrom`` / ``approve`` on a
  destination whose static type declares the ERC-20 interface, or declares the
  called ERC-20 function with its exact signature and no ERC-721 function
  (partial token interfaces such as ``DaiLike``).
* ``erc4626.call`` — EIP-4626 ``deposit`` / ``mint`` / ``withdraw`` /
  ``redeem`` on a destination whose type declares the EIP-4626 interface.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from slither.core.declarations import Contract, Function
from slither.core.solidity_types import UserDefinedType
from slither.slithir.operations import Binary, BinaryType, Index
from slither.core.variables.state_variable import StateVariable

from ..facts import exprs as X
from ..facts import namespaced as N
from ..facts.effects import Effect, FrameNode, Guard
from ..facts.resolver import Frame, Resolver, Site

ERC20_SIGS = {"transfer(address,uint256)", "balanceOf(address)", "transferFrom(address,address,uint256)"}
ERC20_CALLS = {"transfer": "transfer(address,uint256)", "transferFrom": "transferFrom(address,address,uint256)",
               "approve": "approve(address,uint256)"}
ERC721_SIGS = {"ownerOf(uint256)", "safeTransferFrom(address,address,uint256)", "getApproved(uint256)"}
SELECTORS = {"transfer": "a9059cbb", "transferFrom": "23b872dd", "approve": "095ea7b3"}
ERC4626_SIGS = {"asset()", "deposit(uint256,address)", "mint(uint256,address)",
                "withdraw(uint256,address,address)", "redeem(uint256,address,address)"}
EIP20 = "https://eips.ethereum.org/EIPS/eip-20"
EIP4626 = "https://eips.ethereum.org/EIPS/eip-4626"


def _sigs(c: Contract) -> set:
    out = set()
    for f in c.functions:
        out.add(f.solidity_signature)
    for v in c.state_variables:
        if v.visibility == "public":
            out.add(getattr(v, "solidity_signature", None) or f"{v.name}()")
    return out


def balance_getter(c: Contract) -> Optional[Function]:
    """The implemented ``balanceOf(address)`` of a contract (an inherited interface declaration does not
    count: a public ``balanceOf`` mapping implements it without a function body)."""

    f = c.get_function_from_signature("balanceOf(address)")
    return f if f is not None and f.is_implemented else None


def is_balance_mapping(c: Contract, name: str) -> bool:
    """``name`` holds ERC-20 balances of ``c``: the contract implements ``transfer`` and its ``balanceOf``
    reads the mapping (or the mapping is the public ``balanceOf`` itself). A staking ledger called
    ``balanceOf`` in a contract without ``transfer`` is not a token."""

    t = c.get_function_from_signature("transfer(address,uint256)")
    if t is None or not t.is_implemented:
        return False
    f = balance_getter(c)
    if f is not None:
        return any(v.name == name for v in N.read(f))
    return name == "balanceOf"


def dest_contract(ir) -> Optional[Contract]:
    t = getattr(ir.destination, "type", None)
    if isinstance(t, UserDefinedType) and isinstance(t.type, Contract):
        return t.type
    return None


class Rules:
    def __init__(self, resolver: Resolver, dependencies: List[dict]):
        self.R = resolver
        self.deps = dependencies
        self._struct_cache: Dict[int, Optional[dict]] = {}

    # ----------------------------------------------------------- helpers
    def package(self, path: str) -> Optional[dict]:
        for d in self.deps:
            if path.startswith(d["path"].rstrip("/") + "/"):
                return d
        return None

    def _impl_site(self, fn: Function) -> Site:
        return self.R.site_of(fn.source_mapping, fn.contract_declarer, fn)

    def _self(self, frame: Frame) -> X.Inst:
        return X.Inst(frame.instance.id, frame.instance.label)

    # ------------------------------------------------- internal functions
    def internal_call(self, frame: Frame, ir, fn, args, site: Site, guards) -> Optional[List[Effect]]:
        if not isinstance(fn, Function) or not fn.is_implemented:
            return None
        hit = self._oz_erc20(frame, fn, args, site, guards)
        if hit is not None:
            return hit
        return self._structural(frame, fn, args, site, guards)

    def _oz_erc20(self, frame, fn: Function, args, site, guards) -> Optional[List[Effect]]:
        decl = fn.contract_declarer
        if fn.name not in ("_mint", "_burn", "_transfer") or decl.name not in ("ERC20", "ERC20Upgradeable"):
            return None
        impl = self._impl_site(fn)
        pkg = self.package(impl.file)
        if pkg is None or "openzeppelin" not in pkg["package"]:
            return None
        written = {v.name for v in N.written(fn)}
        need = {"_balances", "_totalSupply"} if fn.name != "_transfer" else {"_balances"}
        if not need <= written:
            return None
        note = (f"{pkg['package']}@{pkg['version']} {decl.name}.{fn.name} "
                f"(implementation writes {', '.join(sorted(need))})")
        detail = {"impl_site": impl, "package": pkg}
        tok = self._self(frame)
        if fn.name == "_mint":
            return [Effect("mint", site, frame, guards, token=tok, dst=args[0], amount=args[1],
                           rule="oz.erc20.internal", rule_note=note, function=fn.full_name, detail=detail)]
        if fn.name == "_burn":
            return [Effect("burn", site, frame, guards, token=tok, src=args[0], amount=args[1],
                           rule="oz.erc20.internal", rule_note=note, function=fn.full_name, detail=detail)]
        return [Effect("transfer", site, frame, guards, token=tok, src=args[0], dst=args[1], amount=args[2],
                       rule="oz.erc20.internal", rule_note=note, function=fn.full_name, detail=detail)]

    def _structural_shape(self, fn: Function) -> Optional[dict]:
        if id(fn) in self._struct_cache:
            return self._struct_cache[id(fn)]
        shape = None
        params = [p for p in fn.parameters]
        refs: Dict[str, Tuple[object, object]] = {}
        scalar: List[Tuple[StateVariable, str, object]] = []
        mapping: List[Tuple[StateVariable, object, str, object]] = []
        ns = N.references(N._fn_irs(fn, False))
        for node in fn.nodes:
            for ir in node.irs:
                if isinstance(ir, Index) and isinstance(ir.variable_left, StateVariable):
                    refs[str(ir.lvalue)] = (ir.variable_left, ir.variable_right)
                elif isinstance(ir, Index) and str(ir.variable_left) in ns and str(ns[str(ir.variable_left)].type).startswith("mapping("):
                    refs[str(ir.lvalue)] = (ns[str(ir.variable_left)], ir.variable_right)
                if isinstance(ir, Binary) and ir.type in (BinaryType.ADDITION, BinaryType.SUBTRACTION):
                    lv = ir.lvalue
                    other = ir.variable_right if ir.variable_left is lv or str(ir.variable_left) == str(lv) else ir.variable_left
                    if isinstance(lv, StateVariable) and str(lv.type).startswith("uint"):
                        scalar.append((lv, ir.type.value, other))
                    elif str(lv) in ns and str(ns[str(lv)].type).startswith("uint") and str(lv) not in refs:
                        scalar.append((ns[str(lv)], ir.type.value, other))
                    elif str(lv) in refs:
                        mv, keyv = refs[str(lv)]
                        mapping.append((mv, keyv, ir.type.value, other))
        addr_params = [p for p in params if str(p.type) == "address"]
        bal_updates = [(m, k, op, o) for (m, k, op, o) in mapping if k in addr_params]
        if bal_updates:
            maps = {m.name for (m, _, _, _) in bal_updates}
            if len(maps) == 1:
                m = bal_updates[0][0]
                scalars = [(s, op, o) for (s, op, o) in scalar if s.name != m.name]
                plus = [(k, o) for (_, k, op, o) in bal_updates if op == "+"]
                minus = [(k, o) for (_, k, op, o) in bal_updates if op == "-"]
                if scalars and plus and not minus and all(op == "+" for (_, op, _) in scalars):
                    shape = {"kind": "mint", "to": plus[0][0], "amount": plus[0][1], "supply": scalars[0][0], "balances": m}
                elif scalars and minus and not plus and all(op == "-" for (_, op, _) in scalars):
                    shape = {"kind": "burn", "from": minus[0][0], "amount": minus[0][1], "supply": scalars[0][0], "balances": m}
                elif not scalars and plus and minus and plus[0][0] is not minus[0][0]:
                    shape = {"kind": "transfer", "from": minus[0][0], "to": plus[0][0], "amount": minus[0][1], "balances": m}
        self._struct_cache[id(fn)] = shape
        return shape

    def _structural(self, frame, fn: Function, args, site, guards) -> Optional[List[Effect]]:
        shape = self._structural_shape(fn)
        if shape is None or not self._is_token_balances(frame.instance.contract, shape["balances"]):
            return None
        pidx = {p.name: i for i, p in enumerate(fn.parameters)}

        def arg(v):
            i = pidx.get(getattr(v, "name", None))
            return args[i] if i is not None and i < len(args) else X.Opaque(str(v))

        impl = self._impl_site(fn)
        what = (f"updates supply `{shape['supply'].name}` and balances `{shape['balances'].name}`"
                if "supply" in shape else f"moves balances `{shape['balances'].name}` between two addresses")
        note = f"{fn.contract_declarer.name}.{fn.name} {what}"
        tok = self._self(frame)
        d = {"impl_site": impl, "balances": shape["balances"].name}
        if shape["kind"] == "mint":
            return [Effect("mint", site, frame, guards, token=tok, dst=arg(shape["to"]), amount=arg(shape["amount"]),
                           rule="structural.token-accounting", rule_note=note, function=fn.full_name, detail=d)]
        if shape["kind"] == "burn":
            return [Effect("burn", site, frame, guards, token=tok, src=arg(shape["from"]), amount=arg(shape["amount"]),
                           rule="structural.token-accounting", rule_note=note, function=fn.full_name, detail=d)]
        return [Effect("transfer", site, frame, guards, token=tok, src=arg(shape["from"]), dst=arg(shape["to"]),
                       amount=arg(shape["amount"]), rule="structural.token-accounting", rule_note=note,
                       function=fn.full_name, detail=d)]

    @staticmethod
    def _is_token_balances(c: Contract, mapping: StateVariable) -> bool:
        """The mapping is the token's balance mapping only if ``balanceOf``
        reads it; other address-keyed amounts (debts, pending claims) are
        ledgers, interpreted by the semantics."""

        return is_balance_mapping(c, mapping.name)

    # --------------------------------------------------------- libraries
    def library_call(self, frame: Frame, ir, args, site: Site, guards) -> Optional[List[Effect]]:
        lib = ir.destination
        fn = ir.function
        if isinstance(fn, Function) and getattr(lib, "name", "") == "SafeTransferLib":
            return self._safe_transfer_lib(frame, lib, fn, args, site, guards)
        if not isinstance(fn, Function):
            return None
        impl = self._impl_site(fn)
        pkg = self.package(impl.file)
        if getattr(lib, "name", "") not in ("SafeERC20", "SafeERC20Upgradeable") or pkg is None:
            return self._safe_erc20_like(frame, lib, fn, args, site, guards)
        body = fn.source_mapping.content or ""
        me = self._self(frame)
        note = f"{pkg['package']}@{pkg['version']} {lib.name}.{fn.name}"
        detail = {"impl_site": impl, "package": pkg}
        if fn.name == "safeTransfer" and "transfer" in body:
            return [Effect("transfer", site, frame, guards, token=args[0], src=me, dst=args[1], amount=args[2],
                           rule="oz.safeerc20", rule_note=note + " encodes token.transfer(to, value)",
                           function=fn.full_name, detail=detail)]
        if fn.name == "safeTransferFrom" and "transferFrom" in body:
            return [Effect("transfer", site, frame, guards, token=args[0], src=args[1], dst=args[2], amount=args[3],
                           rule="oz.safeerc20", rule_note=note + " encodes token.transferFrom(from, to, value)",
                           function=fn.full_name, detail=dict(detail, allowance=True))]
        if fn.name in ("forceApprove", "safeApprove", "safeIncreaseAllowance", "safeDecreaseAllowance") and "approve" in body:
            return [Effect("approve", site, frame, guards, token=args[0], src=me, dst=args[1], amount=args[2],
                           rule="oz.safeerc20", rule_note=note + " sets an ERC-20 allowance",
                           function=fn.full_name, detail=detail)]
        return None

    def _safe_erc20_like(self, frame: Frame, lib, fn: Function, args, site: Site, guards) -> Optional[List[Effect]]:
        """Any library's ``safeTransfer`` / ``safeTransferFrom`` / ``safeApprove`` (token, ..., amount) whose
        code, or a library function it calls, encodes the ERC-20 call (``abi.encodeCall(IERC20.transfer, ..)``,
        ``IERC20.transferFrom.selector``, the selector constant)."""

        shapes = {"safeTransfer": ("transfer", 3), "safeTransferFrom": ("transferFrom", 4),
                  "safeApprove": ("approve", 3), "forceApprove": ("approve", 3)}
        if fn.name not in shapes or not getattr(lib, "is_library", False):
            return None
        call, n = shapes[fn.name]
        if len(args) != n or len(fn.parameters) != n:
            return None
        body = " ".join((f.source_mapping.content or "") for f in [fn] + [
            getattr(c, "function", c) for c in fn.all_internal_calls() + list(fn.all_library_calls())
            if isinstance(getattr(c, "function", c), Function)]).lower()
        enc = {"transfer": ("ierc20.transfer,", "ierc20.transfer.selector", "transfer(address,uint256)", ".transfer("),
               "transferFrom": ("ierc20.transferfrom", "transferfrom(address,address,uint256)", ".transferfrom("),
               "approve": ("ierc20.approve", "approve(address,uint256)", ".approve(")}[call]
        if not (SELECTORS[call] in body or any(m in body for m in enc)):
            return None
        impl = self._impl_site(fn)
        me = self._self(frame)
        detail = {"impl_site": impl, "library": lib.name}
        note = f"{lib.name}.{fn.name} ({impl.file.split('/')[-1]}) encodes ERC-20 {call}"
        if call == "transfer":
            return [Effect("transfer", site, frame, guards, token=args[0], src=me, dst=args[1], amount=args[2],
                           rule="safeerc20.library", rule_note=note, function=fn.full_name, detail=detail)]
        if call == "transferFrom":
            return [Effect("transfer", site, frame, guards, token=args[0], src=args[1], dst=args[2], amount=args[3],
                           rule="safeerc20.library", rule_note=note, function=fn.full_name,
                           detail=dict(detail, allowance=True))]
        return [Effect("approve", site, frame, guards, token=args[0], src=me, dst=args[1], amount=args[2],
                       rule="safeerc20.library", rule_note=note, function=fn.full_name, detail=detail)]

    def _safe_transfer_lib(self, frame: Frame, lib, fn: Function, args, site: Site, guards) -> Optional[List[Effect]]:
        """solmate / solady SafeTransferLib: the body is assembly that stores the ERC-20 selector and calls
        the token; the rule requires the selector in the body and the (token, ..., amount) parameter shape."""

        body = (fn.source_mapping.content or "").lower()
        impl = self._impl_site(fn)
        me = self._self(frame)
        detail = {"impl_site": impl, "library": lib.name}
        shapes = {"safeTransfer": ("transfer", 3), "safeTransferFrom": ("transferFrom", 4), "safeApprove": ("approve", 3)}
        if fn.name == "safeTransferETH" and len(args) == 2 and len(fn.parameters) == 2 and "call(" in body:
            return [Effect("native", site, frame, guards, token=X.Const("ETH", "native"), src=me, dst=args[0],
                           amount=args[1], rule="safetransferlib",
                           rule_note=f"{lib.name}.safeTransferETH ({impl.file.split('/')[-1]}) sends ETH with a call",
                           function=fn.full_name, detail=detail)]
        if fn.name not in shapes:
            return None
        call, n = shapes[fn.name]
        if len(args) != n or len(fn.parameters) != n or SELECTORS[call] not in body:
            return None
        note = f"{lib.name}.{fn.name} ({impl.file.split('/')[-1]}) encodes ERC-20 {call} (selector 0x{SELECTORS[call]})"
        if call == "transfer":
            return [Effect("transfer", site, frame, guards, token=args[0], src=me, dst=args[1], amount=args[2],
                           rule="safetransferlib", rule_note=note, function=fn.full_name, detail=detail)]
        if call == "transferFrom":
            return [Effect("transfer", site, frame, guards, token=args[0], src=args[1], dst=args[2], amount=args[3],
                           rule="safetransferlib", rule_note=note, function=fn.full_name, detail=dict(detail, allowance=True))]
        return [Effect("approve", site, frame, guards, token=args[0], src=me, dst=args[1], amount=args[2],
                       rule="safetransferlib", rule_note=note, function=fn.full_name, detail=detail)]

    # ---------------------------------------------------- external calls
    def external_call(self, frame: Frame, ir, dest: X.Expr, args, ret, site: Site, guards, disp=None):
        c = dest_contract(ir)
        if c is None:
            return None
        sigs = _sigs(c)
        name = ir.function_name
        me = self._self(frame)
        iface_site = self.R.site_of(c.source_mapping, c, None)
        pkg = self.package(iface_site.file)
        where = f"{c.name} ({pkg['package']}@{pkg['version']})" if pkg else c.name
        if ERC4626_SIGS <= sigs and name in ("deposit", "mint", "withdraw", "redeem"):
            return [self._erc4626(frame, name, dest, args, site, guards, where, iface_site, disp or [a.render() for a in args])]
        partial = name in ERC20_CALLS and ERC20_CALLS[name] in sigs and not (ERC721_SIGS & sigs)
        if ERC20_SIGS <= sigs or partial:
            detail = {"interface": where, "standard": EIP20, "impl_in_scope": self.R.bound_instance(dest) is not None}
            note = f"ERC-20 {name} on {where}; token implementation outside the analysed instances"
            if not ERC20_SIGS <= sigs:
                note = (f"ERC-20 {ERC20_CALLS[name]} (exact signature) on {where}, which declares only part of the "
                        "ERC-20 interface; token implementation outside the analysed instances")
            if name == "transfer" and len(args) == 2:
                return [Effect("transfer", site, frame, guards, token=dest, src=me, dst=args[0], amount=args[1],
                               rule="erc20.call", rule_note=note, function="transfer(address,uint256)", detail=detail)]
            if name == "transferFrom" and len(args) == 3:
                return [Effect("transfer", site, frame, guards, token=dest, src=args[0], dst=args[1], amount=args[2],
                               rule="erc20.call", rule_note=note, function="transferFrom(address,address,uint256)",
                               detail=dict(detail, allowance=True))]
            if name == "approve" and len(args) == 2:
                return [Effect("approve", site, frame, guards, token=dest, src=me, dst=args[0], amount=args[1],
                               rule="erc20.call", rule_note=note, function="approve(address,uint256)", detail=detail)]
        return None

    def _erc4626(self, frame: Frame, name: str, vault: X.Expr, args, site: Site, guards, where: str, iface_site: Site, disp) -> FrameNode:
        me = self._self(frame)
        asset = X.Getter(vault, "asset", ())
        v = X._r(vault)
        note = f"EIP-4626 {name} via {where}; vault implementation outside the analysed instances"
        detail = {"interface": where, "standard": EIP4626, "iface_site": iface_site}
        node = FrameNode("adapter", frame, site, guards, label=f"ERC-4626 {name}", executor=vault, initiator=me,
                         adapter="erc4626.call", adapter_note=note, detail=dict(detail, function=name))

        def eff(kind, **kw):
            return Effect(kind, site, frame, (), rule="erc4626.call", rule_note=note, function=name, detail=dict(detail), **kw)

        if name == "deposit":
            assets, receiver = args[0], args[1]
            node.items = [eff("transfer", token=asset, src=me, dst=vault, amount=assets),
                          eff("mint", token=vault, dst=receiver, amount=X.Opaque(f"shares returned by {v}.deposit({disp[0]})"))]
        elif name == "mint":
            shares, receiver = args[0], args[1]
            node.items = [eff("transfer", token=asset, src=me, dst=vault, amount=X.Opaque(f"assets returned by {v}.mint({disp[0]})")),
                          eff("mint", token=vault, dst=receiver, amount=shares)]
        elif name == "withdraw":
            assets, receiver, owner = args[0], args[1], args[2]
            node.items = [eff("burn", token=vault, src=owner, amount=X.Opaque(f"shares returned by {v}.withdraw({disp[0]})")),
                          eff("transfer", token=asset, src=vault, dst=receiver, amount=assets)]
            node.detail["owner"] = owner
        else:  # redeem
            shares, receiver, owner = args[0], args[1], args[2]
            node.items = [eff("burn", token=vault, src=owner, amount=shares),
                          eff("transfer", token=asset, src=vault, dst=receiver,
                              amount=X.Opaque(f"assets returned by {v}.redeem({disp[0]})"))]
            node.detail["owner"] = owner
        node.conditions.append(Guard("modifier", f"{v} accepts the {name} (limits, liquidity and fees are defined by the "
                                     "external implementation)", X.Opaque("external"), False, site))
        if name in ("withdraw", "redeem"):
            node.conditions.append(Guard("modifier", f"caller is the owner or has an allowance over the owner's {v} shares (EIP-4626)",
                                         X.Opaque("allowance"), False, site))
        else:
            node.conditions.append(Guard("modifier", f"caller has approved {v} to pull {v}.asset() (EIP-4626)",
                                         X.Opaque("approval"), False, site))
        return node
