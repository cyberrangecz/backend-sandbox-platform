To test generator type into terminal:

1. $ pipenv install -e git+https://__token__:<your_personal_token>@gitlab.fi.muni.cz/api/v4/projects/14820/packages/pypi/simple#egg=generator
2. $ pipenv shell
3. $ python3.8  
4. \>>> from generator import run_test
5. \>>> run_test() 

You can change variables.yml file to set minimal or maximal generated values and set prohibited values too. (min/max/prohibited works only for port and IP)

Structure of generator/variable.yml:
<variable_name>
    - type: <variable_type>  // the only obligatory attribute
      min: <value>
      max: <value>
      prohibited: [<value>,<value>,...]

Print value should be in format:
\>>> ANSIBLE_ARGS='--extra-vars "telnet_port=4 username=Mariellen password=KRxxhcCg Server_IP=21.125.239.66. Client_IP=192.168.1.20. "' vagrant up server