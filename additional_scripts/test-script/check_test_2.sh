#!/usr/bin/env bash

#!/usr/bin/env bash

# A simple script randomly exiting with 0 if random number is correct

random_number=$(( ( RANDOM % 5 )  + 1 ))

if [ $random_number -eq 3 ]; then
  echo "random_number was $random_number ... exiting with returncode 0"
  exit 0  
else
  echo "random_number was $random_number ... exiting with returncode 1"
  exit 1
fi