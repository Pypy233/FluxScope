// SPDX-License-Identifier: MIT
// SYNTHETIC TEST FIXTURE — written for FluxScope tests; not from a real project.
// Vault/Strategy with separate caller, share receiver and share owner roles.
pragma solidity ^0.8.20;

interface IERC20Like {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address who) external view returns (uint256);
}

interface ILendingPool {
    function supply(address asset, uint256 amount, address onBehalfOf) external;
    function withdraw(address asset, uint256 amount, address to) external returns (uint256);
    function balanceOfUnderlying(address who) external view returns (uint256);
}

/// Minimal transferable ledger. The issue/destroy helpers are intentionally
/// not called _mint/_burn: FluxScope recognises them by their state updates.
contract Ledger {
    string public name;
    string public symbol;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(string memory name_, string memory symbol_) {
        name = name_;
        symbol = symbol_;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _move(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        allowance[from][msg.sender] -= amount;
        _move(from, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function _move(address from, address to, uint256 amount) internal {
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
    }

    function _issue(address to, uint256 amount) internal {
        totalSupply += amount;
        balanceOf[to] += amount;
    }

    function _destroy(address from, uint256 amount) internal {
        totalSupply -= amount;
        balanceOf[from] -= amount;
    }
}

contract Strategy is Ledger {
    IERC20Like public immutable asset;
    address public vault;
    ILendingPool public pool;

    constructor(IERC20Like asset_, address vault_, ILendingPool pool_) Ledger("Strategy share", "sU") {
        asset = asset_;
        vault = vault_;
        pool = pool_;
    }

    function deposit(uint256 amount) external returns (uint256 shares) {
        require(msg.sender == vault, "only vault");
        asset.transferFrom(msg.sender, address(this), amount);
        shares = amount;
        _issue(msg.sender, shares);
        asset.approve(address(pool), amount);
        pool.supply(address(asset), amount, address(this));
    }

    function withdraw(uint256 shares, address to) external returns (uint256 amount) {
        _destroy(msg.sender, shares);
        amount = pool.withdraw(address(asset), shares, address(this));
        asset.transfer(to, amount);
    }

    function totalAssets() public view returns (uint256) {
        return asset.balanceOf(address(this)) + pool.balanceOfUnderlying(address(this));
    }
}

contract Vault is Ledger {
    IERC20Like public immutable asset;
    Strategy public strategy;
    address public keeper;

    constructor(IERC20Like asset_, address keeper_) Ledger("Vault share", "vU") {
        asset = asset_;
        keeper = keeper_;
    }

    function setStrategy(Strategy strategy_) external {
        require(msg.sender == keeper, "only keeper");
        strategy = strategy_;
    }

    function totalAssets() public view returns (uint256) {
        return asset.balanceOf(address(this)) + strategy.balanceOf(address(this));
    }

    function enter(uint256 amount, address receiver) external returns (uint256 shares) {
        uint256 supply = totalSupply;
        shares = supply == 0 ? amount : amount * supply / totalAssets();
        asset.transferFrom(msg.sender, address(this), amount);
        _issue(receiver, shares);
    }

    function allocate(uint256 amount) external {
        require(msg.sender == keeper, "only keeper");
        asset.approve(address(strategy), amount);
        strategy.deposit(amount);
    }

    function exit(uint256 shares, address receiver, address owner) external returns (uint256 amount) {
        if (msg.sender != owner) {
            allowance[owner][msg.sender] -= shares;
        }
        amount = shares * totalAssets() / totalSupply;
        _destroy(owner, shares);
        uint256 idle = asset.balanceOf(address(this));
        if (idle < amount) {
            strategy.withdraw(amount - idle, address(this));
        }
        asset.transfer(receiver, amount);
    }
}
