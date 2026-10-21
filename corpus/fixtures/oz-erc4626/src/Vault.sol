// SPDX-License-Identifier: MIT
// Deployment wrapper for FluxScope tests: OpenZeppelin's ERC4626 implementation, unmodified.
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";

contract Vault is ERC4626 {
    constructor(IERC20 asset_) ERC20("Vault share", "vTKN") ERC4626(asset_) {}
}
