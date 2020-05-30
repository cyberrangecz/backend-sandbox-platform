# Ansible role - Kypo-Creator-accounts

This role serves for adding user accounts on the virtual machine and creating a basic file structure in users home directory.

## Requirements

This role requires root access. If you are using it without the Vagrantfile provided, you either need to specify `become` directive as a global or while invoking the role.

```yml
become: yes
```

## Role paramaters

* `add_accounts` (optional) - List of accounts to be added. (default: see [here](defaults/main.yml))

## Example

Example of simple use of the role (all values are default):

```yml
roles:
    - role: kypo-creator-accounts
```

Example of overriding default values:

```yml
roles:
    - role: kypo-creator-accounts
      add_accounts:
          - name: somebody
            password: pa55word
            create_home_filesystem: true
            set_sudo: true
```
