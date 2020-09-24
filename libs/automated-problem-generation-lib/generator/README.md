To test generator type into terminal:

1. $ pipenv install -e "git+ssh://git@gitlab.fi.muni.cz/kypolab/theses/kosc-automated-problem-generation.git@master#egg=generator"
2. $ pipenv shell
3. $ python3.8  
4. \>>> from generator.test import run_test
5. \>>> run_test() 

You can change variables.yml file to set minimal or maximal generated values and set prohibited values too. (min/max/prohibited works only for port and IP)

Structure of generator/variable.yml:
<variable_name>
    - type: <variable_type>  // the only obligatory attribute
      min: <value>
      max: <value>
      prohibited: [<value>,<value>,...]