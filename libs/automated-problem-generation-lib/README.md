To test generator type into terminal:

1. $ pipenv install -e .
2. $ pipenv shell
3. $ python3.8  
4. \>>> from generator import run_test
5. \>>> run_test() 

Print value should be in format:
\>>> ANSIBLE_ARGS='--extra-vars "telnet_port=39312 username=Alaster password=jWMRpDaQ "' vagrant up server