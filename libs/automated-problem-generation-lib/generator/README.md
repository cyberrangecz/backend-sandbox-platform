To test generator type into terminal:

1. $ pipenv install -e git+https://__token__:<your_personal_token>@gitlab.fi.muni.cz/api/v4/projects/14820/packages/pypi/simple#egg=generator
2. $ pipenv shell
3. $ python3.8  
4. \>>> from generator import run_test
5. \>>> run_test() 